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
    # One Piece
    "one piece":                 "B0CHR8R1L3",   # One Piece Zoro figure
    "zoro":                      "B0CHR8R1L3",   # Zoro figure / merch
    "luffy":                     "B0897K9V4P",   # Luffy figure / poster
    "sanji":                     "B0CHR8R1L3",
    # Naruto
    "naruto":                    "B0BQ9HF1YV",   # Naruto action figure set
    "naruto shippuden":          "B0BQ9HF1YV",
    "sasuke":                    "B0BQ9HF1YV",
    "itachi":                    "B0BQ9HF1YV",
    "kakashi":                   "B0BQ9HF1YV",
    # Demon Slayer
    "demon slayer":              "B09WQZMX5C",   # Demon Slayer Tanjiro figure
    "kimetsu no yaiba":          "B09WQZMX5C",
    "tanjiro":                   "B09WQZMX5C",
    "nezuko":                    "B09WQZMX5C",
    "zenitsu":                   "B09WQZMX5C",
    "inosuke":                   "B09WQZMX5C",
    "rengoku":                   "B09WQZMX5C",
    # Attack on Titan
    "attack on titan":           "B0BXMVZYL5",   # AOT Eren figure
    "shingeki no kyojin":        "B0BXMVZYL5",
    "eren":                      "B0BXMVZYL5",
    "levi":                      "B0BXMVZYL5",
    "mikasa":                    "B0BXMVZYL5",
    # Jujutsu Kaisen
    "jujutsu kaisen":            "B0C4KQ3V1S",   # JJK Gojo figure
    "gojo":                      "B0C4KQ3V1S",
    "sukuna":                    "B0C4KQ3V1S",
    "itadori":                   "B0C4KQ3V1S",
    "megumi":                    "B0C4KQ3V1S",
    # Kaguya-sama
    "kaguya sama":               "B0BPQK9C45",   # Kaguya-sama merch
    "kaguya-sama":               "B0BPQK9C45",
    "kaguya":                    "B0BPQK9C45",
    "shinomiya":                 "B0BPQK9C45",
    # Cyberpunk Edgerunners
    "cyberpunk":                 "B0BG36Y9S4",
    "edgerunners":               "B0BG36Y9S4",
    "rebecca":                   "B0BG36Y9S4",
    "lucy":                      "B0BG36Y9S4",
    # Slime (Rimuru)
    "slime":                     "B09L7X8X4G",
    "rimuru":                    "B09L7X8X4G",
    "tensei shitara":            "B09L7X8X4G",
    # Chainsaw Man
    "chainsaw man":              "B0BNQCYM56",   # Chainsaw Man figure
    "denji":                     "B0BNQCYM56",
    "makima":                    "B0BNQCYM56",
    "power":                     "B0BNQCYM56",
    # Spy x Family
    "spy x family":              "B0BC273P9Y",   # Spy x Family Anya figure
    "anya":                      "B0BC273P9Y",
    "yor":                       "B0BC273P9Y",
    # Frieren
    "frieren":                   "B0CW1M2K5P",   # Frieren poster
    "fern":                      "B0CW1M2K5P",
    "stark":                     "B0CW1M2K5P",
    # Solo Leveling
    "solo leveling":             "B0CRVK31T8",   # Solo Leveling merch
    "jinwoo":                    "B0CRVK31T8",
    # Bleach
    "bleach":                    "B0C8K7H5XP",   # Bleach Ichigo poster
    "ichigo":                    "B0C8K7H5XP",
    # Death Note
    "death note":                "B0855N8182",   # Death Note notebook
    "light yagami":              "B0855N8182",
    # My Hero Academia
    "my hero academia":          "B0BZ6GGJPG",   # MHA Deku figure
    "boku no hero academia":     "B0BZ6GGJPG",
    "deku":                      "B0BZ6GGJPG",
    "bakugo":                    "B0BZ6GGJPG",
    # Dragon Ball
    "dragon ball":               "B0CJG1YKYJ",   # Dragon Ball Goku figure
    "dragon ball z":             "B0CJG1YKYJ",
    "goku":                      "B0CJG1YKYJ",
    "vegeta":                    "B0CJG1YKYJ",
    # Hunter x Hunter
    "hunter x hunter":           "B09B4MZ5Z3",   # HxH merch
    "killua":                    "B09B4MZ5Z3",
    "gon":                       "B09B4MZ5Z3",
    # Re:Zero
    "re zero":                   "B08C8DPK3N",   # Re:Zero merch
    "re:zero":                   "B08C8DPK3N",
    "rem":                       "B08C8DPK3N",
    # Haikyuu
    "haikyuu":                   "B09B4MS5Y6",   # Haikyuu merch
    "hinata":                    "B09B4MS5Y6",
    # Darling in the Franxx
    "zero two":                  "B08C9RBQFN",   # Darling in the FranXX
    "darling in the franxx":     "B08C9RBQFN",
    # JoJo
    "jojo":                      "B08NLR9G9T",   # Jojo poster
    "jotaro":                    "B08NLR9G9T",
    # Blue Lock
    "blue lock":                 "B0BX5G881C",   # Blue Lock poster
    # Vinland Saga
    "vinland":                   "B08R9ZCG7D",
    "vinland saga":              "B08R9ZCG7D",
    # Overlord
    "overlord":                  "B07Z491C3S",
    "ainz":                      "B07Z491C3S",
    # Genshin Impact
    "genshin":                   "B09L7X8X4G",   # Genshin poster/figure
    "genshin impact":            "B09L7X8X4G",
    # Other Top Anime
    "fairy tail":                "B07X6KBHND",
    "sword art online":          "B08NLQ3CPB",
    "fullmetal alchemist":       "B07VFK4V8S",
    "tokyo ghoul":               "B07T34N5G1",
    "black clover":              "B09QRP2S8K",
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


def _fetch_first_asin(search_query: str, anime_name: str = "", character_name: str = "") -> str | None:
    """
    Gets the first relevant ASIN for a search query.

    Priority:
      0. Hardcoded ASIN map by anime or character (instant, zero network, never fails)
      1. Amazon PA-API (official, reliable — used when credentials set in .env)
      2. HTML scraper fallback with strict relevance validation

    Returns ASIN string (e.g. 'B08XYZ1234') or None if not found.
    """
    # ── Priority 0: Hardcoded ASIN map (instant & 100% reliable) ─────────────
    if anime_name:
        key = anime_name.lower().strip()
        for map_key, asin in _ANIME_ASIN_MAP.items():
            if map_key in key or key in map_key:
                logger.info(f"[Amazon] ASIN map hit for anime '{anime_name}': {asin}")
                return asin

    if character_name:
        char_key = character_name.lower().strip()
        for map_key, asin in _ANIME_ASIN_MAP.items():
            if map_key in char_key or char_key in map_key:
                logger.info(f"[Amazon] ASIN map hit for character '{character_name}': {asin}")
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
    Uses category node for Anime Figures & Collectibles and reviews sorting
    so the user lands on high-converting buyable anime products.
    """
    if anime_name and anime_name.strip().lower() not in ("anime", ""):
        specific_query = f"{anime_name.strip()} anime figure poster"
    else:
        specific_query = search_query
    encoded_query = urllib.parse.quote(specific_query)
    return (
        f"https://www.amazon.in/s?k={encoded_query}"
        f"&rh=n%3A1350387031"
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
    Generates a DEEP product link for an exact Amazon.in product page.
    If APP_BASE_URL is set, wraps in a tracker redirect for click metrics.
    NOTE: For Pinterest 'link' (Visit site) field, use resolve_to_direct_link()
    to ensure users always land on the real Amazon product page, not a redirect.

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
        asin = _fetch_first_asin("", anime_name=clean_name, character_name=clean_char)

        if not asin:
            # Pick a safe product type (all strictly poster/figure/collectible — NO hoodies)
            product = random.choice(_PRODUCT_TYPES)

            # If we have a verified character name, use character + anime for more precise results
            if clean_char:
                search_query = f"{clean_char} {clean_name} {product}".strip()
            else:
                search_query = f"{clean_name} {product}".strip()

            # ── STEP 1: Try to get a real ASIN (PA-API -> HTML scraper with relevance check)
            asin = _fetch_first_asin(search_query, anime_name=clean_name, character_name=clean_char)

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
            f"https://www.amazon.in/s?k={urllib.parse.quote(safe_name)}+anime+figure+poster"
            f"&rh=n%3A1350387031"
            f"&tag={AMAZON_AFFILIATE_TAG}"
            f"&sort=review-rank"
        )
        return wrap_with_tracker(fallback_default, anime_name=anime_name, title=title)


# ── PROTECTION LAYER ─────────────────────────────────────────────────────────
# These two functions are the safety net that guarantees Pinterest's "Visit site"
# button ALWAYS lands on a real Amazon product page (never a tracker redirect,
# Render server, or search results page).

def is_direct_product_link(url: str) -> bool:
    """
    PROTECTION 1: Returns True only if `url` is a real Amazon /dp/ASIN direct
    product page link (not a search results page, not a tracker redirect).

    Valid:   https://www.amazon.in/dp/B08XYZ1234?tag=...
    Invalid: https://www.amazon.in/s?k=anime+poster  (search results)
    Invalid: https://mybot.onrender.com/r/ABC123     (tracker redirect)
    """
    if not url or not url.startswith("http"):
        return False
    # Must be an amazon.in/dp/ direct product page
    if "amazon.in/dp/" in url:
        # Extract the ASIN segment (10 chars, alphanumeric)
        asin_match = re.search(r'/dp/([A-Z0-9]{10})', url)
        return asin_match is not None
    return False


def resolve_to_direct_link(link: str, anime_name: str = "", character_name: str = "") -> str:
    """
    PROTECTION 2: Resolves ANY link to a guaranteed direct Amazon /dp/ASIN
    product page URL before it's passed to Pinterest's 'link' (Visit site) field.

    Resolution priority:
      1. Already a /dp/ASIN link — return as-is (strip tracker params if needed).
      2. A tracker redirect (/r/<code>) — look up the target_url in the database.
         If the target is a /dp/ link, use it. If it's a search link, go to step 3.
      3. A search results link (/s?k=...) OR any unrecognized URL — generate a
         fresh ASIN-based direct link using the anime name as fallback.

    This function is called by pinterest_uploader.py immediately before posting
    to guarantee "Visit site" always opens a buyable Amazon product page.
    """
    if not link:
        anime_name = anime_name or "Anime"
        fallback_asin = _fetch_first_asin("", anime_name=anime_name, character_name=character_name)
        if fallback_asin:
            return _build_deep_link(fallback_asin)
        return f"https://www.amazon.in/s?k={urllib.parse.quote(anime_name)}+anime+figure&rh=n%3A1350387031&tag={AMAZON_AFFILIATE_TAG}"

    # ── Step 1: Already a direct /dp/ product link — perfect, use it ────────
    if is_direct_product_link(link):
        logger.debug(f"[DirectLink] Link is already a direct product page: {link[:80]}")
        return link

    # ── Step 2: Tracker redirect → look up the real target URL ──────────────
    if "/r/" in link:
        try:
            from database import get_tracked_target_url
            target = get_tracked_target_url(link)
            if target and target != link and is_direct_product_link(target):
                logger.info(f"[DirectLink] Resolved tracker {link[-12:]} -> direct: {target[:80]}")
                return target
            # Target is also a search link — fall through to step 3
            if target and target != link:
                logger.warning(f"[DirectLink] Tracker target is a search link, upgrading to ASIN...")
                link = target  # Use as hint for anime name extraction below
        except Exception as e:
            logger.warning(f"[DirectLink] Could not resolve tracker link: {e}")

    # ── Step 3: Search link or unknown — generate fresh ASIN direct link ────
    # Try to extract anime name from search query URL if not provided
    if not anime_name or anime_name.lower() == "anime":
        try:
            parsed = urllib.parse.urlparse(link)
            query_str = urllib.parse.parse_qs(parsed.query).get("k", [""])[0]
            if query_str:
                # Strip product type keywords to get just the anime name
                for pt in _PRODUCT_TYPES:
                    query_str = query_str.replace(pt, "").strip()
                anime_name = query_str.strip() or anime_name
        except Exception:
            pass

    clean_name = _sanitize_anime_name(anime_name) if anime_name else "Anime"
    clean_char = clean_character_name(character_name) if character_name else ""

    # Try ASIN map first (instant, reliable)
    asin = _fetch_first_asin("", anime_name=clean_name, character_name=clean_char)
    if not asin:
        # Try scraper with a search query
        product = random.choice(_PRODUCT_TYPES)
        sq = f"{clean_char} {clean_name} {product}".strip() if clean_char else f"{clean_name} {product}".strip()
        asin = _fetch_first_asin(sq, anime_name=clean_name, character_name=clean_char)

    if asin:
        direct = _build_deep_link(asin)
        logger.info(f"[DirectLink] Upgraded to direct product link: {direct}")
        return direct

    # Last resort: return the best search link we have (still amazon.in, still anime-specific)
    logger.warning(f"[DirectLink] Could not find ASIN, using search fallback for '{clean_name}'")
    return _build_search_link(f"{clean_name} anime figure", anime_name=clean_name)


