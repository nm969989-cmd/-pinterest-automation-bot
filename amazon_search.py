import urllib.parse
from config import AMAZON_AFFILIATE_TAG
from logger import get_logger

logger = get_logger(__name__)

def generate_amazon_link(anime_name):
    """
    Generates an Amazon search link for merchandise related to the anime,
    including the affiliate tag.
    """
    try:
        # If anime_name is empty, use a generic fallback
        if not anime_name or not anime_name.strip():
            anime_name = "Anime"
            
        search_query = f"{anime_name.strip()} merchandise"
        encoded_query = urllib.parse.quote(search_query)
        
        # Build the URL
        amazon_url = f"https://www.amazon.com/s?k={encoded_query}&tag={AMAZON_AFFILIATE_TAG}"
        logger.info(f"Generated Amazon link for query: '{search_query}'")
        return amazon_url
    except Exception as e:
        logger.error(f"Error generating Amazon link: {str(e)}")
        # Fallback to generic amazon link with tag
        return f"https://www.amazon.com/?tag={AMAZON_AFFILIATE_TAG}"
