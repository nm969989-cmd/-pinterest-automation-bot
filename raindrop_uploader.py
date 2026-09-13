"""
raindrop_uploader.py — Raindrop.io Visual Bookmarking Cross-Poster
==================================================================
Cross-posts pins to Raindrop.io visual collections (Pinterest-like boards).
Each bookmark has:
  - Cover image (high-res anime poster)
  - Title
  - Excerpt / Description
  - Direct destination URL (Amazon affiliate / Pinterest link)
  - Tags

Raindrop.io REST API: https://developer.raindrop.io/v1/raindrops
"""

import time
import requests
from logger import get_logger

logger = get_logger(__name__)

RAINDROP_API_BASE = "https://api.raindrop.io/rest/v1"
_MAX_RETRIES = 3
_RETRY_DELAYS = [0, 5, 15]


def _get_headers() -> dict:
    """Returns auth headers for Raindrop.io API calls."""
    from config import RAINDROP_ACCESS_TOKEN
    if not RAINDROP_ACCESS_TOKEN:
        raise ValueError("RAINDROP_ACCESS_TOKEN is not set in .env")
    return {
        "Authorization": f"Bearer {RAINDROP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def verify_raindrop_token() -> bool:
    """Checks if the RAINDROP_ACCESS_TOKEN is valid."""
    from config import RAINDROP_ACCESS_TOKEN
    if not RAINDROP_ACCESS_TOKEN:
        return False
    try:
        res = requests.get(
            f"{RAINDROP_API_BASE}/user",
            headers=_get_headers(),
            timeout=10,
        )
        return res.status_code == 200 and res.json().get("result", False)
    except Exception as e:
        logger.warning(f"[Raindrop] Token check failed: {e}")
        return False


def get_raindrop_collection_info() -> dict | None:
    """
    Fetches Raindrop collection metadata (title, count, link).
    Returns dict with keys: title, count, url, slug.
    """
    from config import RAINDROP_COLLECTION_ID
    try:
        cid = RAINDROP_COLLECTION_ID or 0
        if cid == 0:
            return {"title": "Unsorted", "count": 0, "url": "https://app.raindrop.io/my/0"}

        res = requests.get(
            f"{RAINDROP_API_BASE}/collection/{cid}",
            headers=_get_headers(),
            timeout=10,
        )
        if res.status_code == 200:
            item = res.json().get("item", {})
            user_name = item.get("creatorRef", {}).get("name", "my")
            slug = item.get("slug", "")
            return {
                "title": item.get("title", "Anime Posters"),
                "count": item.get("count", 0),
                "url": f"https://raindrop.io/{user_name}/{slug}-{cid}" if item.get("public") else f"https://app.raindrop.io/my/{cid}",
                "public": item.get("public", False),
            }
    except Exception as e:
        logger.warning(f"[Raindrop] Could not get collection info: {e}")
    return None


def post_to_raindrop(image_url: str, title: str, description: str,
                     link: str = "", image_path: str = None) -> bool:
    """
    Creates a visual bookmark card on Raindrop.io in the configured collection.

    Args:
        image_url:   Publicly accessible CDN image URL (used as bookmark cover).
        title:       Pin title.
        description: Pin description/excerpt.
        link:        Destination link (Amazon affiliate / Pinterest URL).
        image_path:  Local file path (fallback).

    Returns:
        True on success, False on failure.
    """
    from config import RAINDROP_ENABLED, RAINDROP_COLLECTION_ID

    if not RAINDROP_ENABLED:
        logger.debug("[Raindrop] RAINDROP_ENABLED=false -- skipping cross-post.")
        return False

    # Destination link must be a valid URL. If empty, fallback to Pinterest or image_url.
    target_link = link if (link and link.startswith("http")) else (image_url or "https://www.pinterest.com")
    if not target_link:
        logger.warning("[Raindrop] No destination link or image URL available -- skipping.")
        return False

    tags = ["anime", "poster", "art", "wallpaper", "aesthetic"]

    payload = {
        "link": target_link,
        "title": title[:200] if title else "Anime Poster",
        "excerpt": description[:500] if description else "",
        "tags": tags,
    }

    if image_url and image_url.startswith("http"):
        payload["cover"] = image_url

    if RAINDROP_COLLECTION_ID:
        payload["collection"] = {"$id": int(RAINDROP_COLLECTION_ID)}

    try:
        headers = _get_headers()
    except ValueError as e:
        logger.error(f"[Raindrop] Config error: {e}")
        return False

    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(
                f"{RAINDROP_API_BASE}/raindrop",
                json=payload,
                headers=headers,
                timeout=15,
            )
            if res.status_code in (200, 201):
                item = res.json().get("item", {})
                drop_id = item.get("_id")
                logger.info(f"[Raindrop] Successfully posted bookmark #{drop_id}: '{title}'")
                return True
            else:
                logger.warning(
                    f"[Raindrop] API error HTTP {res.status_code} "
                    f"(attempt {attempt}/{_MAX_RETRIES}): {res.text[:200]}"
                )
        except Exception as e:
            logger.warning(
                f"[Raindrop] Request exception (attempt {attempt}/{_MAX_RETRIES}): {e}"
            )

    logger.error(f"[Raindrop] Failed to post bookmark after {_MAX_RETRIES} attempts.")
    return False


if __name__ == "__main__":
    print("Testing Raindrop.io API...")
    ok = verify_raindrop_token()
    print("Token valid:", ok)
    if ok:
        info = get_raindrop_collection_info()
        print("Collection:", info)
