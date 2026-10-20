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
