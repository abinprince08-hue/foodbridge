"""TinyFish-powered, source-linked organisation research for FoodBridge.

Faster and more reliable than the first version:
  * searches and page fetches run in parallel, with an overall time budget
  * automatic retries with backoff for 429/5xx responses
  * partial results: one failed call no longer ruins the whole report
  * results are checked against the organisation's name to avoid wrong matches
  * NGO reports now include `awards` and `work` highlights
"""
from __future__ import annotations

import ipaddress
import logging
import math
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from urllib.parse import unquote_plus, urlparse, urlunparse

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()
log = logging.getLogger("foodbridge.tinyfish")

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
TIMEOUT = (5, 25)          # connect, read (seconds) per attempt
DEADLINE_SECONDS = 25      # total budget for one research run (keep below your server timeout)
CACHE_TTL = 6 * 60 * 60    # repeat searches within 6 hours are served from memory
MAX_ITEMS = 5

_session = requests.Session()
_session.mount("https://", HTTPAdapter(
    pool_connections=10, pool_maxsize=10,
    max_retries=Retry(total=2, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset({"GET", "POST"}), respect_retry_after_header=True,
                      raise_on_status=False)))

_cache: dict[str, tuple[float, list]] = {}
_cache_lock = threading.Lock()


# --------------------------------------------------------------------------
# URL validation
# --------------------------------------------------------------------------
def validate_public_url(value: str) -> str:
    """Validate that a supplied URL is HTTP(S) and points to a public host."""
    try:
        parsed = urlparse((value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid public http or https URL.") from exc

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a complete public http or https URL.")

    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Local and internal addresses cannot be researched.")

    try:
        try:
            addresses = [ipaddress.ip_address(host)]          # literal IP
        except ValueError:
            records = socket.getaddrinfo(host, None)           # domain name
            addresses = [ipaddress.ip_address(r[4][0]) for r in records]
    except (ValueError, socket.gaierror) as exc:
        raise ValueError("The website hostname could not be resolved. Check the URL.") from exc

    if not addresses:
        raise ValueError("The website hostname did not resolve to a public address.")
    if any(not a.is_global for a in addresses):
        raise ValueError("Private or non-public network addresses cannot be researched.")

    # Rebuild the URL without any user:password@ part.
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, ""))


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------
def _get_api_key() -> str:
    return os.getenv("TINYFISH_API_KEY", "").strip()


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key, "Accept": "application/json", "User-Agent": "FoodBridge/1.0"}


def _api_error_note(exc: Exception, operation: str) -> str:
    """Friendly note for the UI. Raw response bodies are logged, never shown to users."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        log.warning("TinyFish HTTP %s while trying to %s: %s", status, operation,
                    (exc.response.text or "")[:500].replace("\n", " "))
        messages = {
            400: "TinyFish rejected the request.",
            401: "TinyFish authentication failed. Check that TINYFISH_API_KEY is the full, valid key.",
            402: "TinyFish reported a billing or credits problem. Check your TinyFish plan.",
            403: "TinyFish denied access. Check your account permissions.",
            404: "TinyFish endpoint not found. The API URL may have changed.",
            429: "TinyFish rate limit or quota reached. Try again shortly.",
        }
        return messages.get(status, f"TinyFish returned HTTP {status} while trying to {operation}.")
    if isinstance(exc, requests.Timeout):
        return f"TinyFish timed out while trying to {operation}. Try again shortly."
    if isinstance(exc, requests.ConnectionError):
        return f"Could not connect to TinyFish while trying to {operation}."
    log.warning("TinyFish error while trying to %s: %r", operation, exc)
    return f"TinyFish could not {operation}."


def _search(query: str, api_key: str) -> list[dict]:
    key = "s:" + query
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    response = _session.get(SEARCH_URL, params={"query": query, "language": "en", "location": "IN"},
                            headers=_headers(api_key), timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    results = []
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        results = [item for item in payload["results"] if isinstance(item, dict)]

    if results:  # never cache empty/failed lookups
        with _cache_lock:
            if len(_cache) > 500:
                _cache.clear()
            _cache[key] = (now + CACHE_TTL, results)
    return results


def _fetch(urls: list[str], api_key: str) -> list:
    if not urls:
        return []
    response = _session.post(FETCH_URL, json={"urls": urls, "format": "markdown", "links": True},
                             headers=_headers(api_key), timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "pages", "documents"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return []


def _fetch_page(url: str, api_key: str) -> tuple[str, str]:
    """Return (final_url, markdown_text) for one page."""
    fetched = _fetch([url], api_key)
    page = next((p for p in fetched if isinstance(p, dict) and url in (p.get("url"), p.get("final_url"))),
                fetched[0] if fetched else {})
    if isinstance(page, str):
        return url, page
    page = page if isinstance(page, dict) else {}
    text = str(page.get("markdown") or page.get("text") or page.get("content") or page.get("body") or "")
    return (page.get("final_url") or page.get("url") or url), text


def _run_parallel(tasks: dict, budget: float = DEADLINE_SECONDS):
    """Run callables in parallel. Returns (results_by_name, errors, timed_out)."""
    pool = ThreadPoolExecutor(max_workers=max(1, len(tasks)))
    futures = {name: pool.submit(fn) for name, fn in tasks.items()}
    done, pending = wait(list(futures.values()), timeout=budget)
    results, errors = {}, []
    for name, future in futures.items():
        if future in done:
            try:
                results[name] = future.result()
            except Exception as exc:  # one failure must not sink the rest
                errors.append(exc)
        else:
            future.cancel()
    pool.shutdown(wait=False, cancel_futures=True)
    return results, errors, bool(pending)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_NOISE = re.compile(r"cookie|privacy|terms of|subscribe|copyright|all rights|skip to|log ?in|sign ?up|javascript", re.I)
_STOP = {"the", "and", "for", "foundation", "trust", "society", "ngo", "india", "charitable", "restaurant",
         "kitchen", "cafe", "bakery", "pvt", "ltd", "private", "limited"}
AWARD_RE = re.compile(r"\b(award(?:ed|s)?|winner|won|honou?red|recogni[sz]ed|recogni[sz]ation|felicitated|"
                      r"accredited|certified|prize|finalist|best ngo|laureate)\b", re.I)
WORK_RE = re.compile(r"\b(meals?|served|distribut\w+|provid(?:ed|es|ing)|trained|supported|schools?|"
                     r"scholarships?|beneficiar\w+|famil(?:y|ies)|children|women|livelihoods?|health camps?|"
                     r"programmes?|programs?|initiatives?|campaigns?|rescued|feeds?|feeding|workshops?)\b", re.I)
_RATING_A = re.compile(r"(?:rated|rating)\s*:?\s*([1-5](?:\.\d)?)\b", re.I)
_RATING_B = re.compile(r"\b([1-5]\.\d)\s*(?:/\s*5|stars?|★|\(\s*[\d,]+)", re.I)


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    core = [w for w in words if len(w) > 2 and w not in _STOP]
    return core or words


def _mentions(text: str, tokens: list[str]) -> bool:
    """True if enough of the organisation's name appears, to avoid wrong-organisation matches."""
    if not tokens:
        return False
    lowered = text.lower()
    hits = sum(1 for w in tokens if re.search(rf"\b{re.escape(w)}\b", lowered))
    return hits >= max(1, math.ceil(len(tokens) * 0.6))


def _excerpt(text: str, limit: int = 650) -> str:
    for raw in text.splitlines():
        line = _clean(_MD_LINK.sub(r"\1", raw)).strip(" #*>-\t")
        if len(line) >= 60 and not line.startswith(("http", "![")) and not _NOISE.search(line) and line.count("|") < 2:
            return line[:limit]
    return ""


def _sentences(text: str) -> list[str]:
    return [_clean(s) for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _from_results(results: list[dict], tokens: list[str], pattern: re.Pattern) -> list[dict]:
    found = []
    for item in results:
        url, title = str(item.get("url") or ""), _clean(item.get("title"))
        snippet = _clean(item.get("snippet"))
        if not url or not _mentions(f"{title} {snippet} {unquote_plus(url)}", tokens):
            continue
        for sentence in _sentences(f"{title}. {snippet}"):
            if 25 <= len(sentence) <= 320 and pattern.search(sentence):
                found.append({"text": sentence, "title": title or url, "url": url,
                              "site_name": urlparse(url).hostname or ""})
    return found


def _from_text(content: str, url: str, title: str, pattern: re.Pattern) -> list[dict]:
    found = []
    for raw in content.splitlines():
        line = _clean(_MD_LINK.sub(r"\1", raw)).strip(" #*>-\t")
        if 30 <= len(line) <= 300 and not line.startswith("http") and not _NOISE.search(line) and pattern.search(line):
            found.append({"text": line, "title": title, "url": url, "site_name": urlparse(url).hostname or ""})
    return found


def _key(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())[:70]


def _dedupe(items: list[dict], limit: int = MAX_ITEMS) -> list[dict]:
    seen, out = set(), []
    for item in items:
        k = _key(item["text"])
        if k not in seen:
            seen.add(k)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _no_key(kind: str) -> dict:
    subject = "Google review search" if kind == "restaurant" else "NGO profile research"
    return {"kind": kind, "state": "API key not configured", "observations": [], "sources": [],
            "featured_reviews": [], "basic_info": "", "awards": [], "work": [],
            "note": f"Add TINYFISH_API_KEY to the server environment to enable {subject}. Restart the server after changing it."}


def _failure(kind: str, state: str, note: str) -> dict:
    return {"kind": kind, "state": state, "observations": [], "featured_reviews": [], "sources": [],
            "basic_info": "", "awards": [], "work": [], "note": note}


# --------------------------------------------------------------------------
# Restaurants
# --------------------------------------------------------------------------
_REVIEW_WORDS = re.compile(r"review|rating|rated|stars?|★", re.I)
_MAPS_HOSTS = {"google.com", "www.google.com", "maps.google.com", "google.co.in", "www.google.co.in",
               "maps.google.co.in", "maps.app.goo.gl", "goo.gl"}


def _is_maps_url(candidate: str) -> bool:
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    return host in _MAPS_HOSTS and ("/maps" in parsed.path.lower() or host in {"maps.app.goo.gl", "goo.gl"})


def _restaurant_queries(name: str, location: str = "") -> list[str]:
    place = f" {location.strip()}" if location and location.strip() else ""
    return [
        f'site:google.com/maps/place "{name}" reviews{place}',
        f'site:google.com/maps "{name}" ratings{place}',
        f'"{name}" restaurant reviews rating{place}',   # fallback: other review sites
    ]


def research_restaurant(url: str, restaurant_name: str = "", location: str = "") -> dict:
    """Find review excerpts for a restaurant.

    Google Maps results are preferred. If none have usable text, excerpts from other
    sites are shown instead, each clearly labelled with the site it came from.
    """
    api_key = _get_api_key()
    if not api_key:
        return _no_key("restaurant")

    name = (restaurant_name or "").strip()
    if not name:
        try:
            name = urlparse(validate_public_url(url)).hostname or ""
        except ValueError:
            name = ""
    if not name:
        return _failure("restaurant", "Research unavailable", "A restaurant name is needed to search for reviews.")

    queries = _restaurant_queries(name, location)
    tasks = {f"q{i}": (lambda q=q: _search(q, api_key)) for i, q in enumerate(queries)}
    results, errors, timed_out = _run_parallel(tasks)

    if not results:
        note = _api_error_note(errors[0], "search for reviews") if errors else \
            "TinyFish took too long to respond. Try again shortly."
        return _failure("restaurant", "API request failed", note)

    tokens = _tokens(name)
    seen, maps_matches, web_matches = set(), [], []
    for batch in results.values():
        for item in batch:
            candidate = str(item.get("url") or "").strip()
            if not candidate or candidate in seen:
                continue
            title = _clean(item.get("title"))
            snippet = _clean(item.get("snippet") or item.get("description") or item.get("text") or item.get("content"))
            if not _mentions(f"{title} {snippet} {unquote_plus(candidate)}", tokens):
                continue
            seen.add(candidate)
            entry = {"url": candidate, "title": title or "Search result", "snippet": snippet}
            if _is_maps_url(candidate):
                maps_matches.append(entry)
            elif snippet and _REVIEW_WORDS.search(f"{title} {snippet}"):
                web_matches.append(entry)

    maps_have_text = any(m["snippet"] for m in maps_matches)
    chosen = [(m, True) for m in maps_matches[:3]]
    if not maps_have_text:
        chosen += [(w, False) for w in web_matches[:3]]

    featured, sources, rating = [], [], None
    for entry, is_maps in chosen:
        site = "Google Maps" if is_maps else (urlparse(entry["url"]).hostname or "Web")
        sources.append({"url": entry["url"], "title": entry["title"], "site_name": site, "retrieved_at": _now_iso(),
                        "source_type": "Google Search result" if is_maps else "Web search result",
                        "snippet": entry["snippet"]})
        if entry["snippet"]:
            featured.append({"excerpt": entry["snippet"][:420], "title": entry["title"], "url": entry["url"],
                             "site_name": site})
            if rating is None:
                text = f"{entry['snippet']} {entry['title']}"
                m = _RATING_A.search(text) or _RATING_B.search(text)
                rating = float(m.group(1)) if m else None

    if maps_have_text:
        state, kind = "Google review results found", "google_maps"
        note = ("Search excerpts were found, but they may be incomplete or outdated. "
                "Open the linked Google Maps results to check current reviews.")
    elif featured:
        state, kind = "Web review results found", "web"
        note = ("No Google Maps excerpts were available, so these come from other sites (named on each source). "
                "They may be incomplete or outdated.")
    elif maps_matches:
        state, kind = "Google Maps listing found", "google_maps"
        note = "A matching listing was found, but no excerpt text was returned. Open the linked source to read reviews."
    else:
        state, kind = "No Google review excerpts found", "none"
        note = "No matching listing or review excerpts were found. Try the exact restaurant name and locality."
    if errors or timed_out:
        note += " Some searches failed or timed out, so results may be incomplete."

    return {"kind": "restaurant", "state": state, "source_kind": kind, "observations": [],
            "featured_reviews": featured, "sources": sources, "basic_info": "", "awards": [], "work": [],
            "rating": rating, "query": queries[0], "note": note,
            "diagnostics": {"returned": {q: len(r) for q, r in results.items()}, "failed": len(errors)}}


def diagnose_restaurant(name: str, location: str = "") -> dict:
    """Developer helper: show what TinyFish actually returns for each query.

    Run:  python -c "import tinyfish_client as t, json; print(json.dumps(t.diagnose_restaurant('Singh Sahib','Dwarka'), indent=1))"
    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "TINYFISH_API_KEY is not set"}
    out = {}
    for query in _restaurant_queries(name, location):
        try:
            found = _search(query, api_key)
            out[query] = {"count": len(found), "result_fields": sorted(found[0].keys()) if found else [],
                          "urls": [str(r.get("url"))[:120] for r in found[:8]]}
        except Exception as exc:
            code = exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response is not None else type(exc).__name__
            out[query] = {"error": f"{code}: {exc}"[:300]}
    return out


# --------------------------------------------------------------------------
# NGOs
# --------------------------------------------------------------------------
def research_ngo(ngo_name: str, website: str = "") -> dict:
    """Find an NGO's awards, recognition and work, with source links.

    Returns the usual report fields plus:
      awards: [{text, title, url, site_name}]
      work:   [{text, title, url, site_name}]
    """
    api_key = _get_api_key()
    if not api_key:
        return _no_key("ngo")

    name = (ngo_name or "").strip()
    if not name:
        return _failure("ngo", "Research unavailable", "An organisation name is needed.")

    site_url, site_problem = "", ""
    if website and website.strip():
        try:
            site_url = validate_public_url(website)
        except ValueError as exc:
            site_problem = str(exc)  # keep going with search-only research

    tasks = {
        "awards": lambda: _search(f'"{name}" award OR recognition OR honoured OR felicitated', api_key),
        "work": lambda: _search(f'"{name}" programmes OR initiatives OR impact OR "meals served"', api_key),
    }
    if site_url:
        host = urlparse(site_url).hostname
        tasks["site"] = lambda: _fetch_page(site_url, api_key)
        tasks["site_search"] = lambda: _search(f"site:{host} awards OR recognition OR achievements", api_key)

    results, errors, timed_out = _run_parallel(tasks)
    if not results:
        note = _api_error_note(errors[0], "research this NGO") if errors else \
            "TinyFish took too long to respond. Try again shortly."
        return _failure("ngo", "API request failed", note)

    tokens = _tokens(name)
    awards, work, sources, basic_info = [], [], [], ""

    if "site" in results:
        page_url, content = results["site"]
        basic_info = _excerpt(content)
        title = f"{name} official website"
        awards += _from_text(content, page_url, title, AWARD_RE)
        work += _from_text(content, page_url, title, WORK_RE)
        if basic_info:
            sources.append({"url": page_url, "title": title, "site_name": urlparse(page_url).hostname or "",
                            "retrieved_at": _now_iso(), "source_type": "Official website", "snippet": basic_info})

    if site_url:
        # Results restricted to the NGO's own site count even if the name isn't in the snippet.
        for item in results.get("site_search", []):
            awards += _from_results([item], tokens, AWARD_RE) or \
                [{"text": s, "title": _clean(item.get("title")) or str(item.get("url")), "url": str(item.get("url") or ""),
                  "site_name": urlparse(str(item.get("url") or "")).hostname or ""}
                 for s in _sentences(f"{item.get('title') or ''}. {item.get('snippet') or ''}")
                 if 25 <= len(s) <= 320 and AWARD_RE.search(s) and item.get("url")]
    awards += _from_results(results.get("awards", []), tokens, AWARD_RE)
    work += _from_results(results.get("work", []), tokens, WORK_RE)

    awards = _dedupe(awards)
    award_keys = {_key(a["text"]) for a in awards}
    work = _dedupe([w for w in work if _key(w["text"]) not in award_keys])

    # Last resort: Wikipedia, only when everything else came back empty.
    if not (basic_info or awards or work):
        try:
            for item in _search(f'site:en.wikipedia.org/wiki "{name}"', api_key):
                candidate = str(item.get("url") or "")
                if "wikipedia.org/wiki/" not in candidate or not _mentions(f"{item.get('title')} {unquote_plus(candidate)}", tokens):
                    continue
                page_url, content = _fetch_page(validate_public_url(candidate), api_key)
                basic_info = _excerpt(content) or _clean(item.get("snippet"))[:650]
                if basic_info:
                    sources.append({"url": page_url, "title": _clean(item.get("title")) or f"{name} on Wikipedia",
                                    "site_name": "Wikipedia", "retrieved_at": _now_iso(),
                                    "source_type": "Wikipedia", "snippet": basic_info})
                    break
        except (requests.RequestException, ValueError) as exc:
            errors.append(exc)

    seen_urls = {s["url"] for s in sources}
    for entry in awards + work:
        if entry["url"] not in seen_urls:
            seen_urls.add(entry["url"])
            sources.append({"url": entry["url"], "title": entry["title"], "site_name": entry["site_name"],
                            "retrieved_at": _now_iso(), "source_type": "Web search result", "snippet": entry["text"]})

    found = bool(basic_info or awards or work)
    note = ("Highlights are short excerpts from search results and the organisation's own site, and are not "
            "independently verified. Open the linked sources for full context.")
    if not found:
        note = _api_error_note(errors[0], "research this NGO") if errors else \
            "No awards or programme details were found for this organisation."
    elif errors or timed_out:
        note += " Some lookups did not finish, so this may be partial."
    if site_problem:
        note += f" Website skipped: {site_problem}"

    return {"kind": "ngo", "state": "Profile information found" if found else "No profile information found",
            "basic_info": basic_info, "awards": awards, "work": work, "observations": [],
            "featured_reviews": [], "sources": sources, "note": note}
