import urllib.parse
import random
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


def generate_amazon_link(anime_name: str, character_name: str = "") -> str:
    """
    Generates a specific Amazon.in search link for anime merchandise.

    Always includes the word 'anime' in the search so results are relevant
    (avoids generic ads like Ronaldo, Drake, etc. showing up for "wall art").

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
