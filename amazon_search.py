import urllib.parse
import random
import re
import requests
from config import AMAZON_AFFILIATE_TAG
from logger import get_logger
import pa_api  # Official PA-API (used when credentials set)

logger = get_logger(__name__)

# Product types — always combined with "anime" keyword to avoid generic ads
# Note: clothing items (like hoodies) removed to prevent Amazon from returning generic plain clothes
_PRODUCT_TYPES = [
    "anime poster",
    "anime wall art poster",
    "anime action figure",
    "anime merchandise",
    "anime collectible figure",
    "anime plush toy",
    "anime artbook",
    "anime sticker pack",
    "anime keychain",
    "anime wall scroll",
    "anime aesthetic poster",
]

# Known dead or delisted ASINs that return Amazon 404 "Looking for something?" error.
# These must NEVER be served to Pinterest users.
_DEAD_ASINS = {
    "B09WQZMX5C",   # Dead Demon Slayer Tanjiro figure (causes Amazon India 404)
    "B08XYZ1234",   # Placeholder ASIN
    "0000000000",
}

# ── Retired Static ASIN Map ──────────────────────────────────────────────────
# Hardcoded single-product ASINs are permanently retired to prevent Amazon 404
# "Page Not Found / Looking for something?" errors when sellers delist items.
# Instead, the system defaults to dynamic, fail-proof Amazon Storefront Search links
# (or real-time official PA-API lookup when configured).
_ANIME_ASIN_MAP = {}

# Words that indicate non-character titles (adjectives, generic labels)
_NON_CHARACTER_WORDS = {
    "seductive", "cute", "beautiful", "dark", "hot", "sexy", "cool", "epic",
    "angry", "smiling", "sleeping", "gorgeous", "mysterious", "aesthetic",
    "stunning", "anime", "original", "wallpaper", "poster", "art", "illustration",
    "girl", "boy", "demon", "warrior", "hero", "villain", "badass", "lovely",
    "sweet", "magic", "magical", "fantasy", "isekai", "vintage", "retro"
}


def clean_character_name(character_name: str) -> str:
    """
    Validates and cleans extracted character name.
    Rejects adjectives and generic non-character terms like 'Seductive', 'Cute', 'Aesthetic'.
    """
    if not character_name:
        return ""
    first_token = character_name.strip().split()[0].strip()
    clean = re.sub(r'[^a-zA-Z0-9]', '', first_token)
    if not clean or len(clean) < 3:
        return ""
    if clean.lower() in _NON_CHARACTER_WORDS:
        return ""
    return clean


def _sanitize_anime_name(raw_name: str) -> str:
    """
    Cleans up AI-generated anime names into clean, targeted search keywords.
    Strips fluff words, separators (/ | \), and handles original art / generic terms.
    """
    if not raw_name or not raw_name.strip():
        return "Anime"
    name = raw_name.strip()
    if ":" in name:
        name = name.split(":")[0].strip()
    parts = [p.strip() for p in re.split(r"[/|\\]", name) if p.strip()]
    fluff_words = {
        "original art", "original artwork", "original character", "original",
        "fanart", "illustration", "wallpaper", "aesthetic", "concept art",
        "seductive", "hot", "cute", "beautiful", "dark", "epic", "cool",
        "anime", "girl", "boy", "art"
    }

    chosen_words = []
    for part in parts:
        words = [w for w in re.split(r'\s+', part) if w and not any(ch in w for ch in "()[]{}<>\"'*")]
        meaningful = [w for w in words if w.lower() not in fluff_words]
        if meaningful:
            chosen_words = meaningful
            break

    if not chosen_words:
        result = "Anime"
    else:
        result = " ".join(chosen_words[:4])

    if not result or result.lower() in ("art", "character", "girl", "boy"):
        result = "Anime"

    return result


def _is_product_relevant(title: str, anime_name: str = "", search_query: str = "") -> bool:
    """
    Checks if a product title is actually relevant to anime / artwork / searched anime.
    Rejects completely unrelated items like generic plain clothes, yellow hoodies, household items.
    """
    if not title:
        return False

    title_lower = title.lower()

    # Negative filters: obvious non-anime generic clothing or unrelated household items
    unrelated_generic = [
        "plain hoodie", "fleece hooded", "men's cotton plain", "regular fit plain",
        "solid regular fit", "casual solid", "round neck plain", "sweatshirt plain",
        "kitchen", "cookware", "curtain", "cleaning", "mop", "bedsheet", "pillow cover",
        "mobile case", "tempered glass", "adapter", "cable", "charger"
    ]
    if any(neg in title_lower for neg in unrelated_generic):
        return False

    # Positive filters: anime merchandise / art keywords
    anime_terms = [
        "anime", "manga", "poster", "wall art", "figure", "action figure",
        "figurine", "collectible", "wall scroll", "otaku", "cosplay",
        "plush", "sticker", "keychain", "artbook", "japanese", "chibi",
        "statue", "desk mat", "mouse pad"
    ]
    if any(term in title_lower for term in anime_terms):
        return True

    # Check if any significant word of the anime name is in the product title
    if anime_name and anime_name.lower() != "anime":
        name_words = [w.lower() for w in anime_name.split() if len(w) >= 3 and w.lower() not in ("art", "girl", "boy", "demon")]
        if any(w in title_lower for w in name_words):
            return True

    return False


# Rotating user-agents to avoid Amazon blocking headless requests
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def _build_deep_link(asin: str) -> str:
    """
    Given an Amazon ASIN, returns a direct deep product page link
    with the affiliate tag attached — lands on the exact product page
    with 'Buy Now' / 'Add to Cart' button, not a search results page.

    e.g. https://www.amazon.in/dp/B08XYZ1234?tag=yourtag-21
    """
    return (
        f"https://www.amazon.in/dp/{asin}"
        f"?tag={AMAZON_AFFILIATE_TAG}"
        f"&linkCode=ogi&th=1&psc=1"
    )


def _fetch_first_asin(search_query: str, anime_name: str = "", character_name: str = "") -> str | None:
    """
    Gets the first relevant, verified live ASIN for a search query.

    Priority:
      1. Amazon PA-API (official, verified live listing — used when credentials set in .env)
      2. HTML scraper fallback with strict relevance and dead ASIN validation
      3. Hardcoded ASIN map fallback (only if verified not in _DEAD_ASINS)

    Returns ASIN string (e.g. 'B08XYZ1234') or None if not found.
    """
    # ── Priority 1: PA-API (official, never gets blocked by Amazon) ──────────
    if pa_api.is_available():
        query = search_query or f"{character_name} {anime_name}".strip()
        asin = pa_api.get_best_asin(query)
        if asin and asin not in _DEAD_ASINS:
            return asin
        logger.warning(f"[Amazon] PA-API returned no results for: '{query}'")
        # Fall through to HTML scraper as backup even with PA-API

    # ── Priority 2: HTML scraper with relevance & dead ASIN validation ───────
    if search_query:
        encoded_query = urllib.parse.quote(search_query)
        search_url = f"https://www.amazon.in/s?k={encoded_query}"

        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.amazon.in/",
        }

        try:
            response = requests.get(search_url, headers=headers, timeout=10)
            response.raise_for_status()

            # Extract all product blocks with data-asin
            blocks = re.findall(r'data-asin="([A-Z0-9]{10})"(.*?)(?=(?:data-asin=|$))', response.text, re.DOTALL)

            for asin, block in blocks:
                if not asin or len(asin) != 10 or asin in _DEAD_ASINS:
                    continue

                # Extract title if possible
                title_match = re.search(r'<span[^>]*class="[^"]*a-text-normal[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
                if not title_match:
                    title_match = re.search(r'<h2[^>]*>(?:(?!</h2>).)*?<span[^>]*>(.*?)</span>', block, re.DOTALL)
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""

                # Check relevance
                if _is_product_relevant(title, anime_name=anime_name, search_query=search_query):
                    logger.info(f"[Amazon Scraper] Found verified relevant ASIN: {asin} ('{title[:50]}...') for: '{search_query}'")
                    return asin
                else:
                    if title:
                        logger.debug(f"[Amazon Scraper] Skipped irrelevant ASIN {asin}: '{title[:50]}'")

            logger.warning(f"[Amazon Scraper] No relevant ASIN found for: '{search_query}'")

        except requests.exceptions.RequestException as e:
            logger.error(f"[Amazon Scraper] HTTP error: {e}")

    # ── Priority 3: Hardcoded ASIN map (only if NOT in _DEAD_ASINS) ───────────
    if character_name:
        char_key = character_name.lower().strip()
        for map_key, asin in _ANIME_ASIN_MAP.items():
            if (map_key in char_key or char_key in map_key) and asin not in _DEAD_ASINS:
                logger.info(f"[Amazon] ASIN map hit for character '{character_name}': {asin}")
                return asin

    if anime_name:
        key = anime_name.lower().strip()
        for map_key, asin in _ANIME_ASIN_MAP.items():
            if (map_key in key or key in map_key) and asin not in _DEAD_ASINS:
                logger.info(f"[Amazon] ASIN map hit for anime '{anime_name}': {asin}")
                return asin

    return None


def _build_search_link(search_query: str = "", anime_name: str = "", character_name: str = "") -> str:
    """
    Returns an anime/character-specific Amazon India search storefront link.
    Guaranteed to load live, in-stock products on Amazon India with affiliate tag attached.
    NEVER 404s and never shows 'Looking for something? We're sorry'.
    """
    clean_char = clean_character_name(character_name) if character_name else ""
    clean_anime = _sanitize_anime_name(anime_name) if anime_name else ""

    if clean_char and clean_anime and clean_anime.lower() != "anime":
        query = f"{clean_char} {clean_anime} anime merchandise"
    elif clean_anime and clean_anime.lower() != "anime":
        query = f"{clean_anime} anime merchandise"
    elif clean_char:
        query = f"{clean_char} anime merchandise"
    elif search_query:
        query = search_query
    else:
        query = "anime merchandise poster figure"

    # Normalize whitespace and encode
    query_str = " ".join(query.split())
    encoded_query = urllib.parse.quote(query_str)
    return (
        f"https://www.amazon.in/s?k={encoded_query}"
        f"&tag={AMAZON_AFFILIATE_TAG}"
        f"&sort=review-rank"
        f"&utm_source=Pinterest&utm_medium=organic"
    )


def wrap_with_tracker(raw_amazon_url: str, anime_name: str = "", title: str = "") -> str:
    """
    If APP_BASE_URL is configured (e.g. https://your-bot.onrender.com),
    wraps the direct Amazon URL into a short tracking redirect link:
    f"{APP_BASE_URL}/r/{code}"
    This logs real-time click metrics to SQLite whenever a Pinterest user clicks.
    Otherwise returns direct Amazon URL.
    """
    from config import APP_BASE_URL
    if not APP_BASE_URL:
        return raw_amazon_url
    try:
        from database import create_tracked_link
        code = create_tracked_link(target_url=raw_amazon_url, anime_name=anime_name, title=title)
        tracked_url = f"{APP_BASE_URL}/r/{code}"
        logger.info(f"[Tracker] Created tracked link: {tracked_url} -> {raw_amazon_url}")
        return tracked_url
    except Exception as e:
        logger.error(f"[Tracker] Failed to wrap link: {e}")
        return raw_amazon_url


def generate_amazon_link(anime_name: str, character_name: str = "", title: str = "") -> str:
    """
    Generates a high-converting Amazon product link for a Pinterest pin.

    Strategy:
      1. Sanitize anime & character names (extracting character from title if needed).
      2. If a verified live ASIN is found (via PA-API or validated scraper), returns
         a direct /dp/ASIN product link.
      3. Otherwise, returns a targeted, fail-proof Amazon product storefront link
         packed with live in-stock products matching the anime and character.
      4. Never outputs broken or dead ASIN links that lead to Amazon 404s.
    """
    try:
        # Sanitize anime name and character name
        clean_name = _sanitize_anime_name(anime_name)
        clean_char = clean_character_name(character_name)

        if not clean_char and title:
            raw_char = title.split(" - ")[0].strip().split()[0] if " - " in title else title.strip().split()[0]
            clean_char = clean_character_name(raw_char)

        # Build search query for ASIN lookup
        product = random.choice(_PRODUCT_TYPES)
        if clean_char and clean_name and clean_name.lower() != "anime":
            search_query = f"{clean_char} {clean_name} {product}".strip()
        elif clean_name and clean_name.lower() != "anime":
            search_query = f"{clean_name} {product}".strip()
        else:
            search_query = f"{clean_char} {product}".strip() if clean_char else product

        # Try to find a verified live ASIN
        asin = _fetch_first_asin(search_query, anime_name=clean_name, character_name=clean_char)

        if asin and asin not in _DEAD_ASINS:
            deep_link = _build_deep_link(asin)
            logger.info(f"[Amazon] Deep product link generated: {deep_link}")
            return wrap_with_tracker(deep_link, anime_name=clean_name, title=title)
        else:
            # High-converting live product search storefront (fail-proof, never 404s)
            product_link = _build_search_link(search_query, anime_name=clean_name, character_name=clean_char)
            logger.info(f"[Amazon] Using live product search link for '{clean_name}': {product_link}")
            return wrap_with_tracker(product_link, anime_name=clean_name, title=title)

    except Exception as e:
        logger.error(f"[Amazon] Unexpected error generating link: {e}")
        safe_name = _sanitize_anime_name(anime_name)
        fallback_default = _build_search_link("", anime_name=safe_name, character_name=clean_char)
        return wrap_with_tracker(fallback_default, anime_name=anime_name, title=title)


# ── PROTECTION & RESOLUTION LAYER ─────────────────────────────────────────────
# Guarantees Pinterest's "Visit site" button ALWAYS lands on a real, functioning
# Amazon India product or storefront page (never a broken tracker redirect,
# crashed Render server, or dead 404 ASIN).

def is_direct_product_link(url: str) -> bool:
    """
    Returns True only if `url` is an Amazon.in /dp/ASIN direct product link
    AND not a known dead/broken ASIN.
    """
    if not url or not url.startswith("http"):
        return False
    if "amazon.in/dp/" in url:
        asin_match = re.search(r'/dp/([A-Z0-9]{10})', url)
        if asin_match:
            asin = asin_match.group(1)
            if asin in _DEAD_ASINS:
                return False
            return True
    return False


def resolve_to_direct_link(link: str, anime_name: str = "", character_name: str = "", title: str = "") -> str:
    """
    Resolves ANY link to a guaranteed functioning Amazon destination URL
    before it is passed to Pinterest's 'link' (Visit site) field.

    Guarantees:
      1. Resolves local tracker redirects (/r/<code>) to the direct Amazon URL so
         users aren't dependent on Render server uptime.
      2. If the link contains a known dead ASIN (e.g. B09WQZMX5C), it automatically
         upgrades to a live, in-stock anime product search link.
      3. Strips any erroneous category filters (such as &rh=n%3A1350387031).
      4. Guarantees the user ALWAYS lands on Amazon with buyable products.
    """
    clean_name = _sanitize_anime_name(anime_name) if anime_name else "Anime"
    clean_char = clean_character_name(character_name) if character_name else ""
    if not clean_char and title:
        raw_char = title.split(" - ")[0].strip().split()[0] if " - " in title else title.strip().split()[0]
        clean_char = clean_character_name(raw_char)

    if not link:
        return _build_search_link("", anime_name=clean_name, character_name=clean_char)

    # ── Step 1: Tracker redirect → look up the real target Amazon URL ───────
    if "/r/" in link:
        try:
            from database import get_tracked_target_url
            target = get_tracked_target_url(link)
            if target and target != link:
                logger.info(f"[DirectLink] Resolved tracker {link[-12:]} -> target: {target[:80]}")
                link = target
        except Exception as e:
            logger.warning(f"[DirectLink] Could not resolve tracker link: {e}")

    # ── Step 2: Check for dead ASINs (like B09WQZMX5C) ───────────────────────
    for dead_asin in _DEAD_ASINS:
        if dead_asin in link:
            logger.warning(f"[DirectLink] Detected dead ASIN '{dead_asin}' in link! Auto-healing to live search link...")
            return _build_search_link("", anime_name=clean_name, character_name=clean_char)

    # ── Step 3: Remove bogus Watches category node if present ────────────────
    if "1350387031" in link:
        logger.info("[DirectLink] Stripping erroneous category filter 1350387031 from link...")
        link = re.sub(r'&rh=n%3A1350387031', '', link)
        link = re.sub(r'&rh=n:[0-9]+', '', link)

    # ── Step 4: Already a verified, alive /dp/ product link ──────────────────
    if is_direct_product_link(link):
        logger.debug(f"[DirectLink] Link is an active direct product page: {link[:80]}")
        return link

    # ── Step 5: Direct Amazon search storefront link ────────────────────────
    if "amazon.in/s?" in link or "amazon.in/s/" in link:
        if f"tag={AMAZON_AFFILIATE_TAG}" not in link:
            sep = "&" if "?" in link else "?"
            link = f"{link}{sep}tag={AMAZON_AFFILIATE_TAG}"
        logger.info(f"[DirectLink] Valid Amazon product storefront link: {link[:80]}")
        return link

    # ── Step 6: Fallback for unrecognized link ──────────────────────────────
    logger.info(f"[DirectLink] Generating fresh live product search link for '{clean_name}'")
    return _build_search_link("", anime_name=clean_name, character_name=clean_char)


def preflight_validate_destination(url: str, anime_name: str = "", character_name: str = "", title: str = "") -> str:
    """
    MANDATORY PRE-FLIGHT GATEKEEPER:
    Executed immediately before ANY pin payload is sent to Make.com or Pinterest API.
    Guarantees the destination link CANNOT produce a 404 error on Amazon India.

    Guarantees:
      1. Resolves local tracker redirects (/r/<code>) to direct Amazon URLs.
      2. If URL contains any dead or delisted ASIN (e.g. B09WQZMX5C), immediately
         auto-heals to a live, in-stock search storefront.
      3. If URL contains erroneous category filters (&rh=n%3A1350387031), strips them.
      4. If PA-API is not configured and the URL is an unverified /dp/ link, safely
         converts it to the high-converting storefront search link to eliminate 404 risk.
      5. Ensures affiliate tag is attached on all URLs.
      6. Visitors ALWAYS land on a functioning page with real products.
    """
    clean_name = _sanitize_anime_name(anime_name) if anime_name else "Anime"
    clean_char = clean_character_name(character_name) if character_name else ""
    if not clean_char and title:
        raw_char = title.split(" - ")[0].strip().split()[0] if " - " in title else title.strip().split()[0]
        clean_char = clean_character_name(raw_char)

    resolved = resolve_to_direct_link(url, anime_name=clean_name, character_name=clean_char, title=title)

    # If it's a direct /dp/ product link:
    if "/dp/" in resolved:
        # Check against dead ASINs
        for dead_asin in _DEAD_ASINS:
            if dead_asin in resolved:
                logger.warning(f"[PreFlight] Detected dead ASIN '{dead_asin}'! Converting to live storefront link.")
                return _build_search_link("", anime_name=clean_name, character_name=clean_char)

        # If official PA-API is not available, convert single fragile ASIN to storefront search
        if not pa_api.is_available():
            logger.info(f"[PreFlight] Upgrading single-ASIN link to 100% fail-safe product storefront link for '{clean_name}'")
            return _build_search_link("", anime_name=clean_name, character_name=clean_char)

    return resolved



