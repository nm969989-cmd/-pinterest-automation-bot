import urllib.parse
import random
from config import AMAZON_AFFILIATE_TAG
from logger import get_logger

logger = get_logger(__name__)

# Rotate search suffixes so we get varied, relevant anime product categories
_PRODUCT_SUFFIXES = [
    "action figure",
    "poster",
    "wall art",
    "merchandise",
    "keychain",
    "hoodie",
    "plush toy",
    "anime figure collectible",
    "artbook",
    "sticker",
]


def generate_amazon_link(anime_name: str) -> str:
    """
    Generates an Amazon.in search link for anime-related merchandise.
    Rotates through product categories (figures, posters, hoodies, etc.)
    so that each pin links to a relevant, in-stock product search.
    Affiliate tag is appended automatically.
    """
    try:
        if not anime_name or not anime_name.strip():
            anime_name = "Anime"

        # Pick a random product category for variety
        suffix = random.choice(_PRODUCT_SUFFIXES)
        search_query = f"{anime_name.strip()} {suffix}"
        encoded_query = urllib.parse.quote(search_query)

        amazon_url = (
            f"https://www.amazon.in/s?k={encoded_query}"
            f"&tag={AMAZON_AFFILIATE_TAG}"
            f"&utm_source=Pinterest&utm_medium=organic"
        )
        logger.info(f"[Amazon] Generated link for: '{search_query}'")
        return amazon_url

    except Exception as e:
        logger.error(f"[Amazon] Error generating link: {e}")
        return f"https://www.amazon.in/s?k=anime+merchandise&tag={AMAZON_AFFILIATE_TAG}"
