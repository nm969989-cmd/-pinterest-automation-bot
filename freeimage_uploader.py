"""
freeimage_uploader.py — Freeimage.host Dedicated Image Uploading Module
========================================================================
Uploads high-resolution anime art & posters to Freeimage.host via official v1 API.

Every post generates:
  - Public viewer page: https://freeimage.host/i/<id>
  - Direct image CDN link: https://iili.io/<id>.jpg
  - Custom image title and naming
"""

import os
import time
import requests
from logger import get_logger

logger = get_logger(__name__)

_API_URL = "https://freeimage.host/api/1/upload"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_MAX_RETRIES = 3
_RETRY_DELAYS = [0, 4, 12]


def _get_api_key() -> str:
    from config import FREEIMAGE_API_KEY
    return (FREEIMAGE_API_KEY or "6d207e02198a847aa98d0a2a901485a5").strip()


def verify_freeimage_token() -> bool:
    """Verifies API connectivity to Freeimage.host."""
    api_key = _get_api_key()
    if not api_key:
        return False
    try:
        # Check API response
        res = requests.get(
            "https://freeimage.host/api",
            headers=_HEADERS,
            timeout=8
        )
        return res.status_code == 200 and api_key in res.text
    except Exception as e:
        logger.warning(f"[Freeimage] Connectivity check failed: {e}")
        return False


def get_freeimage_info() -> dict:
    """Returns Freeimage.host service info & configured key status."""
    api_key = _get_api_key()
    return {
        "service": "Freeimage.host",
        "api_key_masked": f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "configured",
        "status": "Active" if verify_freeimage_token() else "Reachable",
        "upload_endpoint": _API_URL,
        "profile_url": "https://freeimage.host/muthelyrics"
    }


def post_to_freeimage(image_path: str,
                      title: str = "",
                      anime_name: str = "",
                      affiliate_url: str = "") -> str | None:
    """
    Uploads an image to Freeimage.host.
    Returns the public viewer URL (e.g. 'https://freeimage.host/i/xyz') on success,
    or None on failure.
    """
    if not os.path.exists(image_path):
        logger.error(f"[Freeimage] File not found: {image_path}")
        return None

    api_key = _get_api_key()
    if not api_key:
        logger.error("[Freeimage] FREEIMAGE_API_KEY is not configured.")
        return None

    # Determine filename and title
    clean_title = title.strip()
    if not clean_title:
        clean_title = f"{anime_name} Anime Art Poster" if anime_name else "Anime Aesthetic Poster"

    payload = {
        "key": api_key,
        "action": "upload",
        "format": "json",
        "title": clean_title,
    }

    last_error = None
    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            with open(image_path, "rb") as f:
                files = {"source": (os.path.basename(image_path), f, "image/jpeg")}
                res = requests.post(
                    _API_URL,
                    data=payload,
                    files=files,
                    headers=_HEADERS,
                    timeout=25
                )

            if res.status_code == 200:
                try:
                    data = res.json()
                    img_data = data.get("image", {})
                    viewer_url = img_data.get("url_viewer") or img_data.get("url_seo") or img_data.get("url")
                    if viewer_url:
                        logger.info(f"[Freeimage] Upload successful -> {viewer_url}")
                        return viewer_url
                except Exception as json_err:
                    logger.warning(f"[Freeimage] JSON parse error: {json_err} (raw text: {res.text[:100]})")
            else:
                logger.warning(f"[Freeimage] Upload returned HTTP {res.status_code}: {res.text[:200]}")
                last_error = f"HTTP {res.status_code}"

        except requests.exceptions.RequestException as req_err:
            logger.warning(f"[Freeimage] Attempt {attempt}/{_MAX_RETRIES} network error: {req_err}")
            last_error = str(req_err)
        except Exception as e:
            logger.error(f"[Freeimage] Attempt {attempt}/{_MAX_RETRIES} unexpected error: {e}")
            last_error = str(e)

    logger.error(f"[Freeimage] All upload attempts failed. Last error: {last_error}")
    return None
