"""
imghippo_uploader.py — Imghippo Dedicated Image Uploading Module
===============================================================
Uploads high-resolution anime art & posters to Imghippo via official v1 API.

Every post generates:
  - Permanent direct image link: https://i.imghippo.com/files/<id>.<ext>
  - Custom image title and naming
  - Auto-linked to @muthelyrics account via API Key
"""

import os
import time
import requests
from logger import get_logger

logger = get_logger(__name__)

_API_URL = "https://api.imghippo.com/v1/upload"
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
    from config import IMGHIPPO_API_KEY
    return (IMGHIPPO_API_KEY or "00f11d3e65f34df60a3cf9e47ecf7453").strip()


def verify_imghippo_token() -> bool:
    """Verifies API key validity against Imghippo API without uploading."""
    api_key = _get_api_key()
    if not api_key:
        return False
    try:
        res = requests.post(
            _API_URL,
            data={"api_key": api_key},
            headers=_HEADERS,
            timeout=8
        )
        data = res.json()
        # Message 'Please provide file' confirms the key is authenticated
        return data.get("message") == "Please provide file"
    except Exception as e:
        logger.warning(f"[Imghippo] Verification check failed: {e}")
        return False


def get_imghippo_info() -> dict:
    """Returns Imghippo service info & configured key status."""
    api_key = _get_api_key()
    return {
        "service": "Imghippo",
        "api_key_masked": f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "configured",
        "status": "Active" if verify_imghippo_token() else "Reachable",
        "upload_endpoint": _API_URL,
        "profile_url": "https://www.imghippo.com/dashboard"
    }


def post_to_imghippo(image_path: str,
                     title: str = "",
                     anime_name: str = "",
                     affiliate_url: str = "") -> str | None:
    """
    Uploads an image to Imghippo.
    Supports either a local file path or a public image URL.
    Returns the direct image URL (e.g. 'https://i.imghippo.com/files/xyz.jpg') on success,
    or None on failure.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.error("[Imghippo] IMGHIPPO_API_KEY is not configured.")
        return None

    # Clean title
    clean_title = (title or "").strip()
    if not clean_title:
        clean_title = f"{anime_name} Anime Art Poster" if anime_name else "Anime Aesthetic Poster"

    is_url = image_path.startswith("http://") or image_path.startswith("https://")
    if not is_url and not os.path.exists(image_path):
        logger.error(f"[Imghippo] File not found: {image_path}")
        return None

    last_error = None
    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            payload = {
                "api_key": api_key,
                "title": clean_title,
            }

            if is_url:
                # Download remote image bytes to upload
                img_res = requests.get(image_path, headers=_HEADERS, timeout=20)
                if img_res.status_code != 200:
                    last_error = f"Failed to fetch image URL: HTTP {img_res.status_code}"
                    continue
                files = {"file": ("image.jpg", img_res.content, "image/jpeg")}
            else:
                with open(image_path, "rb") as f:
                    file_content = f.read()
                files = {"file": (os.path.basename(image_path), file_content, "image/jpeg")}

            res = requests.post(_API_URL, data=payload, files=files, headers=_HEADERS, timeout=60)

            if res.status_code == 200:
                try:
                    data = res.json()
                    # Imghippo API returns {"status": "success", "data": {...}}
                    # (NOT {"success": true}) — check both forms for safety
                    is_success = (
                        data.get("status") == "success"
                        or data.get("success") is True
                        or data.get("success") == "success"
                    )
                    if is_success:
                        file_url = (
                            data.get("data", {}).get("url")
                            or data.get("data", {}).get("view_url")
                        )
                        if file_url:
                            logger.info(f"[Imghippo] Upload successful -> {file_url}")
                            return file_url
                        # Success reported but the expected URL is absent. Record the
                        # condition so the final summary does not print None.
                        last_error = f"HTTP 200 success without image URL: {str(data)[:200]}"
                        logger.warning(f"[Imghippo] Success=true but no URL in data: {data}")
                    else:
                        last_error = (
                            "Upload rejected: "
                            f"status={data.get('status')!r}, success={data.get('success')!r}"
                        )
                        logger.warning(
                            f"[Imghippo] Upload not successful. "
                            f"status={data.get('status')!r}, success={data.get('success')!r}, "
                            f"raw={res.text[:300]}"
                        )
                except Exception as json_err:
                    logger.warning(f"[Imghippo] JSON parse error: {json_err} (raw: {res.text[:200]})")
            else:
                logger.warning(f"[Imghippo] Upload returned HTTP {res.status_code}: {res.text[:300]}")
                last_error = f"HTTP {res.status_code}"
                # If out of credits (HTTP 402), abort immediately without wasting 16s on retries and trip circuit breaker
                if res.status_code == 402 or "not enough credits" in res.text.lower():
                    logger.error("[Imghippo] Free tier quota exhausted (0 credits remaining). Aborting retries.")
                    try:
                        from circuit_breaker import trip_breaker
                        trip_breaker("imghippo", "HTTP 402: Free tier quota exhausted (0 credits remaining)", cooldown_hours=24.0)
                    except Exception:
                        pass
                    return None

        except requests.exceptions.RequestException as req_err:
            logger.warning(f"[Imghippo] Attempt {attempt}/{_MAX_RETRIES} network error: {req_err}")
            last_error = str(req_err)
        except Exception as e:
            logger.error(f"[Imghippo] Attempt {attempt}/{_MAX_RETRIES} unexpected error: {e}")
            last_error = str(e)

    logger.error(f"[Imghippo] All upload attempts failed. Last error: {last_error}")
    return None
