"""
imgpile_uploader.py — ImgPile Dedicated Image Uploading Module
==============================================================
Uploads high-resolution anime art, wallpapers, and posters to ImgPile via official v2 API.

API Documentation:
  Endpoint: POST https://imgpile.com/uploads?filename=<filename>
  Headers:
    Authorization: Bearer <API_KEY>
    Content-Type: image/jpeg (or image/png)
  Body: Binary image bytes

Response returns JSON:
  {
    "data": {
      "slug": "...",
      "pageUrl": "https://imgpile.com/m/...",
      "urls": {
        "original": "https://cdn.imgpile.com/f/...",
        "thumb": "..."
      }
    }
  }
"""

import os
import io
import time
import requests
from logger import get_logger

logger = get_logger(__name__)

_API_URL = "https://imgpile.com/uploads"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

_MAX_RETRIES = 3
_RETRY_DELAYS = [0, 4, 12]


def _get_api_key() -> str:
    try:
        from config import IMGPILE_API_KEY
        return (IMGPILE_API_KEY or os.getenv("IMGPILE_API_KEY", "LsGYl3Y6qYncpDADKbBV4ZHFdABVse41H4HfVrEi")).strip()
    except Exception:
        return os.getenv("IMGPILE_API_KEY", "LsGYl3Y6qYncpDADKbBV4ZHFdABVse41H4HfVrEi").strip()


def verify_imgpile_token() -> bool:
    """Verifies API key validity against ImgPile API without uploading a real image."""
    api_key = _get_api_key()
    if not api_key:
        return False
    try:
        # ImgPile returns HTTP 400 for valid key with empty payload, and HTTP 401 for invalid key
        res = requests.post(
            _API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Length": "0"
            },
            data=b"",
            timeout=8
        )
        return res.status_code != 401
    except Exception as e:
        logger.warning(f"[ImgPile] Connectivity check failed: {e}")
        return False


def get_imgpile_info() -> dict:
    """Returns ImgPile service info & configured key status."""
    api_key = _get_api_key()
    return {
        "service": "ImgPile",
        "api_key_masked": f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "configured",
        "status": "Active" if verify_imgpile_token() else "Reachable",
        "upload_endpoint": _API_URL,
        "profile_url": "https://imgpile.com"
    }


def post_to_imgpile(image_path: str,
                    title: str = "",
                    anime_name: str = "",
                    affiliate_url: str = "") -> str | None:
    """
    Uploads an image to ImgPile.
    Supports either a local file path or a public image URL.
    Returns the public viewer URL (e.g. 'https://imgpile.com/m/xyz') on success,
    or None on failure.
    """
    try:
        from config import IMGPILE_ENABLED
        if not IMGPILE_ENABLED:
            logger.debug("[ImgPile] IMGPILE_ENABLED is False — skipping.")
            return None
    except Exception:
        pass

    api_key = _get_api_key()
    if not api_key:
        logger.error("[ImgPile] IMGPILE_API_KEY is not configured.")
        return None

    # Clean title / filename
    clean_title = (title or "").strip()
    if not clean_title:
        clean_title = f"{anime_name} Anime Art Poster" if anime_name else "Anime Aesthetic Poster"
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in clean_title)[:60]
    filename = f"{safe_name}.jpg"

    is_url = str(image_path).startswith(("http://", "https://"))
    if not is_url and not (image_path and os.path.exists(image_path)):
        logger.error(f"[ImgPile] File not found and not a URL: {image_path}")
        return None

    # Load image bytes
    img_bytes = None
    if is_url:
        try:
            dl = requests.get(image_path, headers=_HEADERS, timeout=25)
            if dl.status_code == 200 and dl.content:
                img_bytes = dl.content
            else:
                logger.warning(f"[ImgPile] Failed to download remote image from {image_path[:60]}: HTTP {dl.status_code}")
        except Exception as dl_err:
            logger.warning(f"[ImgPile] Download exception: {dl_err}")
    else:
        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
        except Exception as read_err:
            logger.error(f"[ImgPile] Failed to read local file {image_path}: {read_err}")
            return None

    if not img_bytes:
        logger.error("[ImgPile] No image bytes available for upload.")
        return None

    upload_url = f"{_API_URL}?filename={filename}"
    headers = {
        "User-Agent": _HEADERS["User-Agent"],
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "image/jpeg"
    }

    last_error = None
    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(upload_url, data=img_bytes, headers=headers, timeout=60)

            if res.status_code in (200, 201):
                try:
                    data = res.json()
                    item_data = data.get("data", {})
                    viewer_url = item_data.get("pageUrl")
                    direct_url = item_data.get("urls", {}).get("original")
                    final_url = viewer_url or direct_url
                    if final_url:
                        logger.info(f"[ImgPile] Upload successful -> {final_url} (direct: {direct_url})")
                        return final_url
                except Exception as json_err:
                    logger.warning(f"[ImgPile] JSON parse error: {json_err} (raw: {res.text[:100]})")
            elif res.status_code == 401:
                logger.error("[ImgPile] HTTP 401 Unauthorized: Invalid or expired API key.")
                try:
                    from circuit_breaker import trip_breaker
                    trip_breaker("imgpile", "HTTP 401: Invalid or expired API key", cooldown_hours=48.0)
                except Exception:
                    pass
                return None
            elif res.status_code == 429:
                logger.warning(f"[ImgPile] Rate limited (HTTP 429). Tripping circuit breaker.")
                try:
                    from circuit_breaker import trip_breaker
                    trip_breaker("imgpile", "HTTP 429: Rate limited by ImgPile", cooldown_hours=6.0)
                except Exception:
                    pass
                return None
            else:
                logger.warning(f"[ImgPile] Upload returned HTTP {res.status_code}: {res.text[:200]}")
                last_error = f"HTTP {res.status_code}"

        except requests.exceptions.RequestException as req_err:
            logger.warning(f"[ImgPile] Attempt {attempt}/{_MAX_RETRIES} network error: {req_err}")
            last_error = str(req_err)
        except Exception as e:
            logger.error(f"[ImgPile] Attempt {attempt}/{_MAX_RETRIES} unexpected error: {e}")
            last_error = str(e)

    logger.error(f"[ImgPile] All upload attempts failed. Last error: {last_error}")
    return None

