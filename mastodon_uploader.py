"""
mastodon_uploader.py — Mastodon / Fediverse Cross-Poster
========================================================
Cross-posts pins to Mastodon (Fediverse) visual microblogging.
Each post has:
  - Attached media (high-res anime poster) with alt text description
  - Post text (title, anime name, Amazon/Pinterest link, anime hashtags)
  - Visibility: public

Mastodon REST API:
  - Media Upload: POST /api/v1/media (or /api/v2/media)
  - Status Post:  POST /api/v1/statuses
"""

import os
import re
import time
import requests
from logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [0, 5, 15]


def _get_instance_url() -> str:
    from config import MASTODON_INSTANCE_URL
    return (MASTODON_INSTANCE_URL or "https://mastodon.social").rstrip("/")


def _get_headers() -> dict:
    """Returns auth headers for Mastodon API calls."""
    from config import MASTODON_ACCESS_TOKEN
    if not MASTODON_ACCESS_TOKEN:
        raise ValueError("MASTODON_ACCESS_TOKEN is not set in .env")
    return {
        "Authorization": f"Bearer {MASTODON_ACCESS_TOKEN}",
    }


def verify_mastodon_token() -> bool:
    """Checks if the MASTODON_ACCESS_TOKEN is valid."""
    from config import MASTODON_ACCESS_TOKEN
    if not MASTODON_ACCESS_TOKEN:
        return False
    base_url = _get_instance_url()
    try:
        res = requests.get(
            f"{base_url}/api/v1/accounts/verify_credentials",
            headers=_get_headers(),
            timeout=10,
        )
        return res.status_code == 200 and "id" in res.json()
    except Exception as e:
        logger.warning(f"[Mastodon] Token check failed: {e}")
        return False


def get_mastodon_profile_info() -> dict | None:
    """
    Fetches Mastodon account profile details (username, url, followers, posts).
    """
    base_url = _get_instance_url()
    try:
        res = requests.get(
            f"{base_url}/api/v1/accounts/verify_credentials",
            headers=_get_headers(),
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            return {
                "username": data.get("username", ""),
                "display_name": data.get("display_name", data.get("username", "")),
                "url": data.get("url", f"{base_url}/@{data.get('username', '')}"),
                "followers_count": data.get("followers_count", 0),
                "statuses_count": data.get("statuses_count", 0),
            }
    except Exception as e:
        logger.warning(f"[Mastodon] Could not get profile info: {e}")
    return None


def _clean_tags(title: str) -> str:
    """Generates clean Mastodon hashtags from title and common anime tags."""
    base_tags = ["#anime", "#animeart", "#wallpaper", "#aesthetic"]
    # Extract alphanumeric words from title for specific hashtags
    words = re.findall(r'[a-zA-Z0-9]{4,}', title)
    extra_tags = []
    for w in words[:3]:
        tag = f"#{w.capitalize()}"
        if tag.lower() not in [t.lower() for t in base_tags] and tag.lower() not in [t.lower() for t in extra_tags]:
            extra_tags.append(tag)
    all_tags = base_tags + extra_tags
    return " ".join(all_tags[:6])


def _upload_media(image_path: str = None, image_url: str = None, description: str = "") -> str | None:
    """
    Uploads an image to Mastodon media endpoint and returns media_id.
    """
    base_url = _get_instance_url()
    media_url = f"{base_url}/api/v1/media"
    headers = _get_headers()

    image_bytes = None
    filename = "anime_poster.jpg"

    if image_path and os.path.isfile(image_path):
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            filename = os.path.basename(image_path)
        except Exception as e:
            logger.warning(f"[Mastodon] Could not read local file {image_path}: {e}")

    if not image_bytes and image_url:
        try:
            dl_res = requests.get(
                image_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.pinterest.com/",
                },
                timeout=15
            )
            if dl_res.status_code == 200:
                image_bytes = dl_res.content
                filename = image_url.split("/")[-1].split("?")[0] or "anime_poster.jpg"
            else:
                logger.warning(f"[Mastodon] Image download returned HTTP {dl_res.status_code} for {image_url}")
        except Exception as e:
            logger.warning(f"[Mastodon] Could not download image {image_url}: {e}")

    if not image_bytes:
        logger.error("[Mastodon] No valid image file or URL available to upload.")
        return None

    # Alt text description (cap at 400 chars)
    alt_text = (description or "Anime Aesthetic Poster")[:400]

    files = {
        "file": (filename, image_bytes, "image/jpeg"),
    }
    data = {
        "description": alt_text,
    }

    res = requests.post(media_url, headers=headers, files=files, data=data, timeout=30)
    if res.status_code in (200, 202):
        media_data = res.json()
        media_id = media_data.get("id")
        logger.info(f"[Mastodon] Media uploaded successfully: ID={media_id}")
        return str(media_id)
    else:
        if res.status_code == 429:
            from circuit_breaker import trip_breaker
            trip_breaker("mastodon", f"HTTP 429 Rate Limit: {res.text[:80]}", cooldown_hours=6.0)
        elif res.status_code == 403:
            from circuit_breaker import trip_breaker
            trip_breaker("mastodon", f"HTTP 403 Forbidden: {res.text[:80]}", cooldown_hours=12.0)
        logger.error(f"[Mastodon] Media upload failed ({res.status_code}): {res.text}")
        return None


def post_to_mastodon(image_url: str, title: str, description: str = "",
                     link: str = "", image_path: str = None) -> str | None:
    """
    Cross-posts an anime image to Mastodon with text, link, and hashtags.

    Args:
        image_url:   Public CDN URL of the image.
        title:       Pin / anime title.
        description: Short description or caption.
        link:        Destination link (Amazon affiliate / Pinterest).
        image_path:  Local image path if available.

    Returns:
        Post URL string on success, or None on failure.
    """
    from config import MASTODON_ENABLED
    if not MASTODON_ENABLED:
        logger.debug("[Mastodon] Cross-posting disabled via MASTODON_ENABLED=false.")
        return None

    base_url = _get_instance_url()
    headers = _get_headers()
    headers["Content-Type"] = "application/json"

    # Step 1: Upload media
    media_id = _upload_media(image_path=image_path, image_url=image_url, description=title)
    if not media_id:
        logger.warning("[Mastodon] Proceeding without media attachment (text-only fallback).")

    # Step 2: Format status text
    # Mastodon default limit is 500 chars
    hashtags = _clean_tags(title)
    parts = [f"✨ {title}"]
    if description and len(description) > 5:
        # Truncate description to keep room for link + tags
        short_desc = description[:140] + ("..." if len(description) > 140 else "")
        parts.append(short_desc)
    if link:
        parts.append(f"🔗 {link}")
    parts.append(hashtags)

    status_text = "\n\n".join(parts)
    if len(status_text) > 490:
        status_text = status_text[:485] + "..."

    payload = {
        "status": status_text,
        "visibility": "public",
    }
    if media_id:
        payload["media_ids"] = [media_id]

    status_endpoint = f"{base_url}/api/v1/statuses"

    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(status_endpoint, headers=headers, json=payload, timeout=15)
            if res.status_code in (200, 201):
                post_data = res.json()
                post_url = post_data.get("url") or f"{base_url}/statuses/{post_data.get('id')}"
                logger.info(f"[Mastodon] Successfully posted: {post_url}")
                return post_url
            elif res.status_code == 429:
                from circuit_breaker import trip_breaker
                trip_breaker("mastodon", f"HTTP 429 Rate Limit: {res.text[:80]}", cooldown_hours=6.0)
                wait_sec = int(res.headers.get("Retry-After", 10))
                logger.warning(f"[Mastodon] Rate limited. Breaker tripped. Waiting {wait_sec}s...")
                time.sleep(wait_sec)
            elif res.status_code == 403:
                from circuit_breaker import trip_breaker
                trip_breaker("mastodon", f"HTTP 403 Forbidden: {res.text[:80]}", cooldown_hours=12.0)
                logger.warning(f"[Mastodon] Attempt {attempt} failed (403): {res.text}")
            else:
                logger.warning(f"[Mastodon] Attempt {attempt} failed ({res.status_code}): {res.text}")
        except Exception as e:
            logger.warning(f"[Mastodon] Attempt {attempt} exception: {e}")

    logger.error("[Mastodon] All posting attempts failed.")
    return None
