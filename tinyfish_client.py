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
    """Reject generic place/listing snippets so they are not shown as customer reviews."""
    text = re.sub(r"\\s+", " ", (text or "")).strip()
    if len(text) < 45:
        return False

    lower = text.lower()
    listing_phrases = (
        "directions", "open now", "closed now", "business profile",
        "website", "get directions", "popular times", "reviews for",
        "google maps", "place details", "located at",
    )
    if any(phrase in lower for phrase in listing_phrases):
        return False

    # Prefer snippets that read like an individual's experience, rather than a listing.
    review_signals = (
        "i ", "i'm ", "i've ", "we ", "my ", "our ",
        "food was", "service was", "staff was", "staff were",
        "we ordered", "i ordered", "visited", "experience",
        "delicious", "wait time", "portion", "friendly",
        "not worth", "highly recommend", "would recommend",
    )
    return any(signal in lower for signal in review_signals)


def research_restaurant(url: str, restaurant_name: str = "") -> dict:
    """Find review text for a restaurant; never label generic Maps listings as reviews."""
    api_key = _get_api_key()
    if not api_key:
        return _no_key("restaurant")

    try:
        safe_url = validate_public_url(url)
        name = (restaurant_name or urlparse(safe_url).hostname or "Restaurant").strip()

        # Search specifically for review text, not just the Maps place card.
        queries = [
            f'site:google.com/maps/place "{name}" "food was" OR "service was" OR "I visited"',
            f'site:google.com/maps/place "{name}" customer review',
            f'"{name}" Google Maps review customer experience',
        ]

        candidates = []
        seen_urls = set()
        for query in queries:
            for item in _search(query, api_key):
                candidate = str(item.get("url") or "").strip()
                parsed = urlparse(candidate)
                host = (parsed.hostname or "").lower()
                is_google_maps = (
                    host in {"google.com", "www.google.com", "maps.google.com"}
                    and "/maps" in parsed.path
                )
                if not is_google_maps or candidate in seen_urls:
                    continue
                seen_urls.add(candidate)
                candidates.append(item)

        featured, sources = [], []
        for item in candidates:
            snippet = re.sub(r"\\s+", " ", str(item.get("snippet") or "")).strip()
            # Do not display a listing description, address, or rating summary as a review.
            if not _looks_like_customer_review(snippet):
                continue

            candidate = str(item.get("url") or "").strip()
            title = str(item.get("title") or f"Customer review for {name}")
            featured.append({
                "excerpt": snippet[:420],
                "title": title,
                "url": candidate,
            })
            sources.append({
                "url": candidate,
                "title": title,
                "site_name": "Google Maps",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source_type": "Google Maps review search excerpt",
                "snippet": snippet[:420],
            })
            if len(featured) >= 5:
                break

        if featured:
            state = "Review excerpts found"
            note = (
                "These are review-like excerpts returned by TinyFish search, not a complete "
                "feed of Google Maps reviews. Open the source to verify the review and its context."
            )
        else:
            state = "No review text found"
            note = (
                "TinyFish did not return verifiable customer-review text for this restaurant. "
                "Google Maps search results often expose the place listing rather than individual "
                "reviews. No place descriptions or rating summaries were mislabeled as reviews."
            )

        return {
            "kind": "restaurant",
            "state": state,
            "observations": [],
            "featured_reviews": featured,
            "sources": sources,
            "basic_info": "",
            "query": queries,
            "note": note,
        }

    except requests.RequestException as exc:
        return {
            "kind": "restaurant",
            "state": "API request failed",
            "observations": [],
            "featured_reviews": [],
            "sources": [],
            "basic_info": "",
            "note": _api_error_note(exc, "search Google customer reviews"),
        }
    except (ValueError, TypeError) as exc:
        return {
            "kind": "restaurant",
            "state": "Research unavailable",
            "observations": [],
            "featured_reviews": [],
            "sources": [],
            "basic_info": "",
            "note": str(exc),
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
