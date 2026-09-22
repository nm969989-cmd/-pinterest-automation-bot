"""
imgbb_uploader.py — ImgBB Dedicated Image Uploading Module
===========================================================
Uploads high-resolution anime art & posters to ImgBB via official v1 API.

Every post generates:
  - Public viewer page: https://ibb.co/<id>
  - Direct image CDN link: https://i.ibb.co/<folder>/<name>.<ext>
  - Thumbnail URL
  - Auto-linked to @muthelyrics account via API Key
"""

import os
import time
import requests
from logger import get_logger

logger = get_logger(__name__)

_API_URL = "https://api.imgbb.com/1/upload"
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
    from config import IMGBB_API_KEY
    return (IMGBB_API_KEY or "1506c60298ac253357a0d04c40ea4bd7").strip()


def verify_imgbb_token() -> bool:
    """Verifies API key validity against ImgBB API without uploading."""
    api_key = _get_api_key()
    if not api_key:
        return False
    try:
        res = requests.post(
            _API_URL,
            data={"key": api_key},
            headers=_HEADERS,
            timeout=8
        )
        data = res.json()
        # Code 130 means "Empty upload source" -> Key is valid and recognized!
        # Code 100 means "Invalid API v1 key" -> Rejected
        return data.get("error", {}).get("code") == 130
    except Exception as e:
        logger.warning(f"[ImgBB] Connectivity check failed: {e}")
        return False


def get_imgbb_info() -> dict:
    """Returns ImgBB service info & configured key status."""
    api_key = _get_api_key()
    return {
        "service": "ImgBB",
        "api_key_masked": f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "configured",
        "status": "Active" if verify_imgbb_token() else "Reachable",
        "upload_endpoint": _API_URL,
        "profile_url": "https://muthelyrics.imgbb.com/"
    }


def post_to_imgbb(image_path: str,
                  title: str = "",
                  anime_name: str = "",
                  affiliate_url: str = "") -> str | None:
    """
    Uploads an image to ImgBB.
    Supports either a local file path or a public image URL.
    Returns the public viewer URL (e.g. 'https://ibb.co/xyz') on success,
    or None on failure.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("[ImgBB] IMGBB_API_KEY is not configured.")
        return None

    # Clean title/name
    clean_title = (title or "").strip()
    if not clean_title:
        clean_title = f"{anime_name} Anime Art Poster" if anime_name else "Anime Aesthetic Poster"
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in clean_title)[:60]

    is_url = image_path.startswith("http://") or image_path.startswith("https://")
    if not is_url and not os.path.exists(image_path):
        logger.error(f"[ImgBB] File not found: {image_path}")
        return None

    last_error = None
    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            payload = {
                "key": api_key,
                "name": safe_name,
            }

            if is_url:
                import io
                try:
                    dl = requests.get(image_path, headers=_HEADERS, timeout=20)
                    if dl.status_code == 200 and dl.content:
                        files = {"image": (f"{safe_name}.jpg", io.BytesIO(dl.content), "image/jpeg")}
                        res = requests.post(_API_URL, data=payload, files=files, headers=_HEADERS, timeout=60)
                    else:
                        payload["image"] = image_path
                        res = requests.post(_API_URL, data=payload, headers=_HEADERS, timeout=45)
                except Exception as dl_err:
                    payload["image"] = image_path
                    res = requests.post(_API_URL, data=payload, headers=_HEADERS, timeout=45)
            else:
                with open(image_path, "rb") as f:
                    files = {"image": (os.path.basename(image_path), f, "image/jpeg")}
                    res = requests.post(_API_URL, data=payload, files=files, headers=_HEADERS, timeout=60)

            if res.status_code == 200:
                try:
                    data = res.json()
                    img_data = data.get("data", {})
                    viewer_url = img_data.get("url_viewer") or img_data.get("url")
                    if viewer_url:
                        logger.info(f"[ImgBB] Upload successful -> {viewer_url} (direct: {img_data.get('url')})")
                        return viewer_url
                except Exception as json_err:
                    logger.warning(f"[ImgBB] JSON parse error: {json_err} (raw: {res.text[:100]})")
            else:
                logger.warning(f"[ImgBB] Upload returned HTTP {res.status_code}: {res.text[:200]}")
                last_error = f"HTTP {res.status_code}"
                # If rate limited (HTTP 429), trip circuit breaker — retries just waste quota
                if res.status_code == 429 or "too many requests" in res.text.lower() or "rate limit" in res.text.lower():
                    logger.error("[ImgBB] Rate limited (HTTP 429). Tripping circuit breaker.")
                    try:
                        from circuit_breaker import trip_breaker
                        trip_breaker("imgbb", "HTTP 429: Rate limited by ImgBB", cooldown_hours=6.0)
                    except Exception:
                        pass
                    return None
                # If forbidden (code 103: Datacenter IP block by Cloudflare/ImgBB), abort immediately and trip circuit breaker
                if "103" in res.text or "forbidden" in res.text.lower() or res.status_code in (401, 403):
                    logger.error("[ImgBB] Access forbidden (Code 103) — ImgBB blocks cloud hosting IP addresses. Aborting retries.")
                    try:
                        from circuit_breaker import trip_breaker
                        # Cloud server IP blocks by ImgBB typically last days/weeks — use 72h cooldown
                        trip_breaker("imgbb", "HTTP 400 Code 103: Server IP blocked by ImgBB", cooldown_hours=72.0)
                    except Exception:
                        pass
                    return None

        except requests.exceptions.RequestException as req_err:
            logger.warning(f"[ImgBB] Attempt {attempt}/{_MAX_RETRIES} network error: {req_err}")
            last_error = str(req_err)
        except Exception as e:
            logger.error(f"[ImgBB] Attempt {attempt}/{_MAX_RETRIES} unexpected error: {e}")
            last_error = str(e)

    logger.error(f"[ImgBB] All upload attempts failed. Last error: {last_error}")
    return None
