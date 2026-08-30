import urllib.parse
import random
import re
import requests
from config import AMAZON_AFFILIATE_TAG
from logger import get_logger

logger = get_logger(__name__)

# Product types — always combined with "anime" keyword to avoid generic ads
_PRODUCT_TYPES = [
    "anime poster",
    "anime wall art poster",
    "anime action figure",
    "anime merchandise",
    "anime hoodie",
    "anime plush toy",
    "anime artbook",
    "anime sticker pack",
    "anime keychain",
    "anime collectible figure",
]

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


def _fetch_first_asin(search_query: str) -> str | None:
    """
    Scrapes the first product ASIN from an Amazon India search results page
    for the given query.

    Returns the ASIN string (e.g. 'B08XYZ1234') or None if not found.
    Note: Amazon may block this in production; use PA-API for reliability.
    """
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

        # Amazon embeds ASIN in data attributes: data-asin="B08XYZ1234"
        # Pick first non-empty ASIN from the search results
        asins = re.findall(r'data-asin="([A-Z0-9]{10})"', response.text)
        # Filter out empty strings or junk values
        valid_asins = [a for a in asins if len(a) == 10 and a != "0000000000"]

        if valid_asins:
            asin = valid_asins[0]
            logger.info(f"[Amazon] Found ASIN: {asin} for query: '{search_query}'")
            return asin
        else:
            logger.warning(f"[Amazon] No ASIN found in search results for: '{search_query}'")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"[Amazon] HTTP error fetching ASIN: {e}")
        return None


def _build_search_link(search_query: str) -> str:
    """
    Fallback: returns a generic Amazon search link if ASIN lookup fails.
    """
    encoded_query = urllib.parse.quote(search_query)
    return (
        f"https://www.amazon.in/s?k={encoded_query}"
        f"&tag={AMAZON_AFFILIATE_TAG}"
        f"&utm_source=Pinterest&utm_medium=organic"
    )


def generate_amazon_link(anime_name: str, character_name: str = "") -> str:
    """
    Generates a DEEP product link for an exact Amazon.in product page.

    Strategy:
      1. Build a targeted search query (anime name + product type + character).
      2. Fetch the first search result ASIN from Amazon's search page.
      3. Return a direct /dp/ASIN link (deep link) so users land on
         the exact product with 'Buy Now' ready — not a search results page.
      4. Falls back to a generic search link if ASIN lookup fails.

    Args:
        anime_name:      The anime series name (e.g. "Demon Slayer: Kimetsu no Yaiba")
        character_name:  Optional character name for more specific results (e.g. "Zenitsu")
    """
    try:
        if not anime_name or not anime_name.strip():
            anime_name = "Anime"

        # Shorten very long anime names to their core title
        name = anime_name.strip()
        if len(name) > 40:
            name = name.split(":")[0].strip()  # e.g. "Demon Slayer: Kimetsu no Yaiba" → "Demon Slayer"

        # Pick a random product type (all include "anime" keyword)
        product = random.choice(_PRODUCT_TYPES)

        # If we have a specific character, use character + show for more precise results
        if character_name and character_name.strip():
            char = character_name.strip().split()[0]  # first name only
            search_query = f"{char} {name} {product}"
        else:
            search_query = f"{name} {product}"

        # ── STEP 1: Try to get a real ASIN for a deep product link ──────────
        asin = _fetch_first_asin(search_query)

        if asin:
            # [OK] Deep product link — lands directly on the "Buy Now" product page
            deep_link = _build_deep_link(asin)
            logger.info(f"[Amazon] Deep link generated: {deep_link}")
            return deep_link
        else:
            # [FALLBACK] Search link — ASIN lookup failed
            fallback_link = _build_search_link(search_query)
            logger.warning(f"[Amazon] Using fallback search link: {fallback_link}")
            return fallback_link

    except Exception as e:
        logger.error(f"[Amazon] Unexpected error generating link: {e}")
        return f"https://www.amazon.in/s?k=anime+merchandise&tag={AMAZON_AFFILIATE_TAG}"
