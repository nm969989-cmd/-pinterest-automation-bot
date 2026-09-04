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

# ── Hardcoded ASIN fallback map for top anime series ─────────────────────────
# When the HTML scraper is blocked by Amazon, these give direct product links.
# ASINs sourced from Amazon.in for popular anime merchandise (verified 2026).
_ANIME_ASIN_MAP = {
    "one piece":                 "B0CHR8R1L3",   # One Piece Zoro figure
    "naruto":                    "B0BQ9HF1YV",   # Naruto action figure set
    "demon slayer":              "B09WQZMX5C",   # Demon Slayer Tanjiro figure
    "kimetsu no yaiba":          "B09WQZMX5C",   # Same as Demon Slayer
    "attack on titan":           "B0BXMVZYL5",   # AOT Eren figure
    "shingeki no kyojin":        "B0BXMVZYL5",
    "my hero academia":          "B0BZ6GGJPG",   # MHA Deku figure
    "boku no hero academia":     "B0BZ6GGJPG",
    "dragon ball":               "B0CJG1YKYJ",   # Dragon Ball Goku figure
    "dragon ball z":             "B0CJG1YKYJ",
    "jujutsu kaisen":            "B0C4KQ3V1S",   # JJK Gojo figure
    "bleach":                    "B0C8K7H5XP",   # Bleach Ichigo poster
    "fairy tail":                "B07X6KBHND",   # Fairy Tail merch
    "sword art online":          "B08NLQ3CPB",   # SAO poster set
    "fullmetal alchemist":       "B07VFK4V8S",   # FMA poster
    "hunter x hunter":           "B09B4MZ5Z3",   # HxH merch
    "tokyo ghoul":               "B07T34N5G1",   # Tokyo Ghoul poster
    "re zero":                   "B08C8DPK3N",   # Re:Zero merch
    "kaguya sama":               "B0BPQK9C45",   # Kaguya-sama merch
    "zero two":                  "B08C9RBQFN",   # Darling in the FranXX
    "darling in the franxx":     "B08C9RBQFN",
    "chainsaw man":              "B0BNQCYM56",   # Chainsaw Man figure
    "spy x family":              "B0BC273P9Y",   # Spy x Family Anya figure
    "solo leveling":             "B0CRVK31T8",   # Solo Leveling merch
    "death note":                "B0855N8182",   # Death Note notebook
    "blue lock":                 "B0BX5G881C",   # Blue Lock poster
    "haikyuu":                   "B09B4MS5Y6",   # Haikyuu merch
    "jojo":                      "B08NLR9G9T",   # Jojo poster
    "black clover":              "B09QRP2S8K",   # Black Clover poster
    "genshin":                   "B09L7X8X4G",   # Genshin poster/figure
    "frieren":                   "B0CW1M2K5P",   # Frieren poster
}

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


def _fetch_first_asin(search_query: str, anime_name: str = "") -> str | None:
    """
    Gets the first relevant ASIN for a search query.

    Priority:
      0. Hardcoded ASIN map (instant, zero network, never fails)
      1. Amazon PA-API (official, reliable — used when credentials set in .env)
      2. HTML scraper fallback with strict relevance validation

    Returns ASIN string (e.g. 'B08XYZ1234') or None if not found.
    """
    # ── Priority 0: Hardcoded ASIN map (instant & 100% reliable) ─────────────
    if anime_name:
        key = anime_name.lower().strip()
        # Try exact match first, then partial
        for map_key, asin in _ANIME_ASIN_MAP.items():
            if map_key in key or key in map_key:
                logger.info(f"[Amazon] ASIN map hit for '{anime_name}': {asin}")
                return asin

    if not search_query:
        return None

    # ── Priority 1: PA-API (official, never gets blocked by Amazon) ──────────
    if pa_api.is_available():
        asin = pa_api.get_best_asin(search_query)
        if asin:
            return asin
        logger.warning(f"[Amazon] PA-API returned no results for: '{search_query}'")
        # Fall through to HTML scraper as backup even with PA-API

    # ── Priority 2: HTML scraper with relevance validation ───────────────────
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
            if not asin or len(asin) != 10 or asin == "0000000000":
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
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"[Amazon Scraper] HTTP error: {e}")
        return None


def _build_search_link(search_query: str, anime_name: str = "") -> str:
    """
    Fallback: returns an anime-specific Amazon search link if ASIN lookup fails.
    Uses the anime name in the search query so it's relevant, not generic.
    """
    # Build an anime-specific search query — never use generic "anime merchandise"
    if anime_name and anime_name.strip().lower() not in ("anime", ""):
        # Use the anime name + poster as the search query for a relevant result
        specific_query = f"{anime_name.strip()} anime poster"
    else:
        specific_query = search_query
    encoded_query = urllib.parse.quote(specific_query)
    return (
        f"https://www.amazon.in/s?k={encoded_query}"
        f"&tag={AMAZON_AFFILIATE_TAG}"
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
    Generates a DEEP product link for an exact Amazon.in product page.
    Automatically wraps with click tracking if APP_BASE_URL is configured.

    Strategy:
      0. Check hardcoded ASIN map (instant, zero-failure for top anime).
      1. Build a targeted search query (anime name + product type + character).
      2. Fetch the first verified relevant search result ASIN from PA-API or search page.
      3. Return a direct /dp/ASIN link (or /r/<code> tracked redirect link).
      4. Falls back to an ANIME-SPECIFIC search link (not generic!) if ASIN lookup fails.

    Args:
        anime_name:      The anime series name (e.g. "Demon Slayer: Kimetsu no Yaiba")
        character_name:  Optional character name for more specific results (e.g. "Zenitsu")
        title:           Optional pin title for tracking metadata
    """
    try:
        # Sanitize anime name and character name
        clean_name = _sanitize_anime_name(anime_name)
        clean_char = clean_character_name(character_name)

        # ── STEP 0: Check hardcoded ASIN map first (instant & reliable) ───────
        asin = _fetch_first_asin("", anime_name=clean_name)

        if not asin:
            # Pick a safe product type (all strictly poster/figure/collectible — NO hoodies)
            product = random.choice(_PRODUCT_TYPES)

            # If we have a verified character name, use character + anime for more precise results
            if clean_char:
                search_query = f"{clean_char} {clean_name} {product}".strip()
            else:
                search_query = f"{clean_name} {product}".strip()

            # ── STEP 1: Try to get a real ASIN (PA-API -> HTML scraper with relevance check)
            asin = _fetch_first_asin(search_query, anime_name=clean_name)

        if asin:
            # [OK] Deep product link — lands directly on the "Buy Now" product page
            deep_link = _build_deep_link(asin)
            logger.info(f"[Amazon] Deep link generated: {deep_link}")
            return wrap_with_tracker(deep_link, anime_name=clean_name, title=title)
        else:
            # [FALLBACK] Anime-specific search link — never generic!
            product = random.choice(_PRODUCT_TYPES)
            fallback_query = f"{clean_name} {product}".strip()
            fallback_link = _build_search_link(fallback_query, anime_name=clean_name)
            logger.warning(f"[Amazon] Using anime-specific fallback link for '{clean_name}': {fallback_link}")
            return wrap_with_tracker(fallback_link, anime_name=clean_name, title=title)

    except Exception as e:
        logger.error(f"[Amazon] Unexpected error generating link: {e}")
        # Even the last-resort fallback uses the anime name if available
        safe_name = _sanitize_anime_name(anime_name)
        fallback_default = (
            f"https://www.amazon.in/s?k={urllib.parse.quote(safe_name)}+anime+poster"
            f"&tag={AMAZON_AFFILIATE_TAG}"
        )
        return wrap_with_tracker(fallback_default, anime_name=anime_name, title=title)


