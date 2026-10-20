"""TinyFish-powered, source-linked organisation research for FoodBridge."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

# Loads TINYFISH_API_KEY from the project's .env file, if present.
# Environment variables already set by the hosting environment are preserved.
load_dotenv()

SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"
TIMEOUT = (10, 60)


def validate_public_url(value: str) -> str:
    """Validate that a supplied URL is HTTP(S) and points to a public host."""
    try:
        parsed = urlparse((value or "").strip())
    except ValueError as exc:
        raise ValueError("Enter a valid public http or https URL.") from exc

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a complete public http or https URL.")

    host = parsed.hostname.rstrip(".").lower()
    if (
        host in {"localhost", "localhost.localdomain"}
        or host.endswith((".local", ".internal", ".localhost"))
    ):
        raise ValueError("Local and internal addresses cannot be researched.")

    try:
        # Literal IPs need no DNS lookup. Domain names must resolve successfully.
        if re.fullmatch(r"[0-9a-fA-F:.]+", host):
            addresses = [ipaddress.ip_address(host)]
        else:
            records = socket.getaddrinfo(host, None)
            addresses = [ipaddress.ip_address(record[4][0]) for record in records]
    except (ValueError, socket.gaierror) as exc:
        raise ValueError("The website hostname could not be resolved. Check the URL.") from exc

    if not addresses:
        raise ValueError("The website hostname did not resolve to a public address.")
    if any(not address.is_global for address in addresses):
        raise ValueError("Private or non-public network addresses cannot be researched.")

    return parsed.geturl()


def _headers(api_key: str) -> dict[str, str]:
    return {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "FoodBridge/1.0",
    }


def _api_error_note(exc: Exception, operation: str) -> str:
    """Return a useful error note, including TinyFish's HTTP response details."""
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        if response is not None:
            status = response.status_code
            # Keep the message compact; never include request headers/API keys.
            details = (response.text or "").strip().replace("\r", " ").replace("\n", " ")[:500]
            if not details:
                details = "No response body was provided."

            if status == 401:
                return (
                    "TinyFish authentication failed (HTTP 401). Check that "
                    "TINYFISH_API_KEY contains the full, valid API key. "
                    f"Response: {details}"
                )
            if status == 402:
                return (
                    "TinyFish returned HTTP 402. Check your account plan, credits, "
                    f"or billing status. Response: {details}"
                )
            if status == 403:
                return (
                    "TinyFish denied this request (HTTP 403). Check account access "
                    f"and API permissions. Response: {details}"
                )
            if status == 404:
                return (
                    f"TinyFish endpoint or resource was not found (HTTP 404) while "
                    f"trying to {operation}. Check the endpoint and request. "
                    f"Response: {details}"
                )
            if status == 429:
                return (
                    "TinyFish rate limit or quota may have been reached (HTTP 429). "
                    f"Wait briefly and retry. Response: {details}"
                )
            if status == 400:
                return (
                    f"TinyFish rejected the request (HTTP 400) while trying to "
                    f"{operation}. Check the request parameters. Response: {details}"
                )
            return (
                f"TinyFish returned HTTP {status} while trying to {operation}. "
                f"Response: {details}"
            )

        return f"TinyFish HTTP error while trying to {operation}: {exc}"

    if isinstance(exc, requests.Timeout):
        return (
            f"TinyFish timed out while trying to {operation}. "
            "Check your connection and retry."
        )

    if isinstance(exc, requests.ConnectionError):
        return (
            f"Could not connect to TinyFish while trying to {operation}. "
            "Check internet access, DNS, or firewall settings."
        )

    return f"TinyFish could not {operation} ({type(exc).__name__}): {exc}"


def _get_api_key() -> str:
    """Read the key at call time so app servers can load environment settings first."""
    return os.getenv("TINYFISH_API_KEY", "").strip()


def _search(query: str, api_key: str) -> list[dict]:
    response = requests.get(
        SEARCH_URL,
        params={"query": query, "language": "en", "location": "IN"},
        headers=_headers(api_key),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]
    return []


def _fetch(urls: list[str], api_key: str) -> list:
    if not urls:
        return []

    response = requests.post(
        FETCH_URL,
        json={"urls": urls, "format": "markdown", "links": True},
        headers=_headers(api_key),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "pages", "documents"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def _page_text(page) -> str:
    if isinstance(page, str):
        return page
    if isinstance(page, dict):
        return str(
            page.get("markdown")
            or page.get("text")
            or page.get("content")
            or page.get("body")
            or ""
        )
    return ""


def _get_page(fetched: list, url: str, index: int):
    for page in fetched:
        if isinstance(page, dict) and (
            page.get("url") == url or page.get("final_url") == url
        ):
            return page
    return fetched[index] if index < len(fetched) else {}


def _excerpt(text: str, limit: int = 650) -> str:
    lines = [re.sub(r"\s+", " ", line).strip(" #\t") for line in text.splitlines()]
    lines = [
        line for line in lines
        if len(line) >= 45 and not line.startswith(("http://", "https://", "["))
    ]
    return lines[0][:limit] if lines else ""


def _no_key(kind: str) -> dict:
    subject = "Google review search" if kind == "restaurant" else "NGO profile research"
    return {
        "kind": kind,
        "state": "API key not configured",
        "observations": [],
        "sources": [],
        "featured_reviews": [],
        "basic_info": "",
        "note": (
            "Add TINYFISH_API_KEY to the Python server environment or project's "
            f".env file to enable {subject}. Restart the server after changing it."
        ),
    }


def _looks_like_customer_review(text: str) -> bool:
    """Accept only plausible customer-review excerpts; reject login walls and place cards."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) < 45:
        return False

    lower = text.lower()
    blocked_phrases = (
        "sign in to continue", "sign in to google", "log in to continue",
        "login to continue", "you must sign in", "please sign in",
        "google accounts", "before you continue to google", "consent.google.com",
        "enable javascript", "unusual traffic", "verify you are human",
        "directions", "open now", "closed now", "business profile",
        "get directions", "popular times", "place details", "located at",
        "website", "reviews for", "google maps",
    )
    if any(phrase in lower for phrase in blocked_phrases):
        return False

    # A snippet must look like a person's experience, not a business description.
    review_signals = (
        "i ", "i'm ", "i've ", "we ", "my ", "our ",
        "food was", "service was", "staff was", "staff were",
        "we ordered", "i ordered", "visited", "experience",
        "delicious", "wait time", "portion", "friendly",
        "not worth", "highly recommend", "would recommend",
        "the food", "the staff", "the service", "ordered the",
    )
    return any(signal in lower for signal in review_signals)


def _review_source_host(url: str) -> str:
    """Return a recognised public review host, or an empty string."""
    host = (urlparse(url).hostname or "").lower()
    allowed = (
        "google.com", "tripadvisor.com", "restaurantguru.com",
        "justdial.com", "zomato.com", "mouthshut.com", "yelp.com",
        "wanderlog.com", "magicpin.in",
    )
    return next((domain for domain in allowed if host == domain or host.endswith("." + domain)), "")


def _extract_review_paragraphs(content: str) -> list[str]:
    """Conservatively extract review-like paragraphs from fetched public pages."""
    if not content:
        return []
    boilerplate = (
        "sign in to continue", "sign in to google", "log in to continue",
        "login to continue", "you must sign in", "please sign in",
        "before you continue to google", "enable javascript",
        "verify you are human", "unusual traffic", "cookie policy",
        "privacy policy", "terms of service", "write a review",
        "read more reviews", "see all reviews", "sort by", "get directions",
        "open now", "closed now", "popular times", "claim this business",
        "all rights reserved", "javascript is required",
    )
    results, seen = [], set()
    for raw in re.split(r"[\r\n]+", content):
        text = re.sub(r"\s+", " ", raw).strip(" #>*-\t")
        if not 55 <= len(text) <= 650:
            continue
        lower = text.lower()
        if any(phrase in lower for phrase in boilerplate):
            continue
        if lower.startswith(("http://", "https://", "cookie", "menu", "home", "photos")):
            continue
        if not _looks_like_customer_review(text):
            continue
        key = lower[:180]
        if key in seen:
            continue
        seen.add(key)
        results.append(text[:420])
        if len(results) >= 8:
            break
    return results


def research_restaurant(url: str, restaurant_name: str = "") -> dict:
    """Search public review sources and return only review-like text that can be verified."""
    api_key = _get_api_key()
    if not api_key:
        return _no_key("restaurant")

    try:
        safe_url = validate_public_url(url)
        name = (restaurant_name or urlparse(safe_url).hostname or "Restaurant").strip()
        queries = [
            f'"{name}" restaurant customer reviews',
            f'"{name}" restaurant review experience',
            f'site:tripadvisor.com "{name}" reviews',
            f'site:restaurantguru.com "{name}" reviews',
            f'site:justdial.com "{name}" reviews',
            f'site:zomato.com "{name}" reviews',
            f'site:google.com/maps/place "{name}" review',
        ]

        candidates, seen_urls, search_errors = [], set(), []
        for query in queries:
            try:
                for item in _search(query, api_key):
                    candidate = str(item.get("url") or "").strip()
                    if not candidate:
                        continue
                    host = _review_source_host(candidate)
                    if not host or candidate in seen_urls:
                        continue
                    try:
                        candidate = validate_public_url(candidate)
                    except ValueError:
                        continue
                    seen_urls.add(candidate)
                    candidates.append({
                        "url": candidate,
                        "title": str(item.get("title") or f"Reviews for {name}"),
                        "site_name": host,
                        "snippet": re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip(),
                    })
            except requests.RequestException as exc:
                search_errors.append(_api_error_note(exc, "search public restaurant reviews"))

        featured, sources, seen_excerpts = [], [], set()
        for item in candidates:
            snippet = item["snippet"]
            if _looks_like_customer_review(snippet):
                key = re.sub(r"\W+", " ", snippet.lower())[:180]
                if key not in seen_excerpts:
                    seen_excerpts.add(key)
                    featured.append({"excerpt": snippet[:420], "title": item["title"], "url": item["url"]})
                    sources.append({
                        "url": item["url"], "title": item["title"], "site_name": item["site_name"],
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "source_type": "Review search excerpt", "snippet": snippet[:420],
                    })

        fetch_candidates = candidates[:6]
        try:
            fetched = _fetch([item["url"] for item in fetch_candidates], api_key)
        except requests.RequestException as exc:
            fetched = []
            search_errors.append(_api_error_note(exc, "fetch public review pages"))

        for index, item in enumerate(fetch_candidates):
            page = _get_page(fetched, item["url"], index)
            content = _page_text(page)
            page_url = (
                (page.get("final_url") or page.get("url") or item["url"])
                if isinstance(page, dict) else item["url"]
            )
            if any(term in content.lower() for term in (
                "sign in to continue", "login to continue", "before you continue to google",
                "verify you are human", "unusual traffic",
            )):
                continue
            for excerpt in _extract_review_paragraphs(content):
                key = re.sub(r"\W+", " ", excerpt.lower())[:180]
                if key in seen_excerpts:
                    continue
                seen_excerpts.add(key)
                featured.append({"excerpt": excerpt, "title": item["title"], "url": page_url})
                sources.append({
                    "url": page_url, "title": item["title"], "site_name": item["site_name"],
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "source_type": "Fetched public review page excerpt", "snippet": excerpt,
                })
                if len(featured) >= 5:
                    break
            if len(featured) >= 5:
                break

        if featured:
            state = "Review excerpts found"
            note = (
                "These are review-like excerpts extracted from public search results or fetched pages, "
                "not a complete feed of customer reviews. Open each source to verify its context. "
                "Some review sites may block automated access."
            )
        else:
            state = "No review text found"
            note = (
                "TinyFish could not retrieve verifiable customer-review text from the public sources "
                "it returned. Some review sites require JavaScript, sign-in, or block automated access. "
                "No generic place descriptions or login prompts were labelled as reviews."
            )
            if search_errors:
                note += " Latest retrieval issue: " + search_errors[-1]

        return {
            "kind": "restaurant", "state": state, "observations": [],
            "featured_reviews": featured[:5], "sources": sources[:5], "basic_info": "",
            "query": queries, "note": note,
        }

    except requests.RequestException as exc:
        return {
            "kind": "restaurant", "state": "API request failed", "observations": [],
            "featured_reviews": [], "sources": [], "basic_info": "",
            "note": _api_error_note(exc, "search and fetch public customer reviews"),
        }
    except (ValueError, TypeError) as exc:
        return {
            "kind": "restaurant", "state": "Research unavailable", "observations": [],
            "featured_reviews": [], "sources": [], "basic_info": "", "note": str(exc),
        }


def research_ngo(ngo_name: str, website: str = "") -> dict:
    """Fetch an NGO's website; use a Wikipedia search result as a fallback."""
    api_key = _get_api_key()
    if not api_key:
        return _no_key("ngo")

    try:
        source_candidates = []
        if website and website.strip():
            validated = validate_public_url(website)
            source_candidates.append({
                "url": validated,
                "title": f"{ngo_name} official website",
                "site_name": urlparse(validated).hostname,
                "snippet": "Organisation website supplied in its profile.",
            })

        sources, basic_info = [], ""
        fetch_note = ""

        if source_candidates:
            candidate = source_candidates[0]
            try:
                fetched = _fetch([candidate["url"]], api_key)
                page = _get_page(fetched, candidate["url"], 0)
                content = _page_text(page)
                page_url = (
                    (page.get("final_url") or page.get("url") or candidate["url"])
                    if isinstance(page, dict) else candidate["url"]
                )
                basic_info = _excerpt(content)
                if basic_info:
                    sources.append({
                        "url": page_url,
                        "title": candidate["title"],
                        "site_name": candidate["site_name"],
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "source_type": "Official website",
                        "snippet": basic_info,
                    })
            except requests.RequestException as exc:
                # Keep fallback research available, but retain the fetch failure if fallback fails.
                fetch_note = _api_error_note(exc, "retrieve the NGO's official website")

        # Search Wikipedia only when no usable excerpt was found on the supplied site.
        if not basic_info:
            for item in _search(f'site:en.wikipedia.org/wiki "{ngo_name}"', api_key):
                candidate_url = str(item.get("url") or "")
                if "wikipedia.org/wiki/" not in candidate_url:
                    continue
                try:
                    candidate_url = validate_public_url(candidate_url)
                    page = _get_page(_fetch([candidate_url], api_key), candidate_url, 0)
                except ValueError:
                    continue
                content = _page_text(page)
                page_url = (
                    (page.get("final_url") or page.get("url") or candidate_url)
                    if isinstance(page, dict) else candidate_url
                )
                basic_info = _excerpt(content) or re.sub(
                    r"\s+", " ", str(item.get("snippet") or "")
                ).strip()[:650]
                if basic_info:
                    sources.append({
                        "url": page_url,
                        "title": item.get("title") or f"{ngo_name} on Wikipedia",
                        "site_name": "Wikipedia",
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "source_type": "Wikipedia",
                        "snippet": basic_info,
                    })
                    break

        note = (
            "Basic page excerpts only. Check the linked source for the organisation's current "
            "and complete information."
        )
        if not basic_info and fetch_note:
            note = fetch_note

        return {
            "kind": "ngo",
            "state": "Profile information found" if basic_info else "No profile information found",
            "basic_info": basic_info,
            "observations": [],
            "featured_reviews": [],
            "sources": sources,
            "note": note,
        }

    except requests.RequestException as exc:
        return {
            "kind": "ngo",
            "state": "API request failed",
            "basic_info": "",
            "observations": [],
            "featured_reviews": [],
            "sources": [],
            "note": _api_error_note(exc, "research this NGO profile"),
        }
    except (ValueError, TypeError) as exc:
        return {
            "kind": "ngo",
            "state": "Research unavailable",
            "basic_info": "",
            "observations": [],
            "featured_reviews": [],
            "sources": [],
            "note": str(exc),
        }
