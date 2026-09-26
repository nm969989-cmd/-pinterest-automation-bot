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
    """Verifies the Freeimage.host API key with a real 1x1 probe upload.

    The public GET /api docs endpoint always answers HTTP 200 for anybody and
    does not validate the key, so it cannot be used for verification. A minimal
    probe upload exercises the real upload endpoint with the configured key: valid
    and invalid keys produce different structured responses.
    """
    api_key = _get_api_key()
    if not api_key:
        return False
    try:
        probe = _probe_payload()
        res = requests.post(
            _API_URL,
            data={"key": api_key, "action": "upload", "format": "json"},
            files={"source": ("graphify-key-probe.png", probe, "image/png")},
            headers=_HEADERS,
            timeout=25,
        )
        return _is_valid_key_response(res, api_key)
    except Exception as e:
        logger.warning(f"[Freeimage] Connectivity check failed: {e}")
        return False


def _probe_payload() -> bytes:
    """Return a minimal valid 1x1 PNG used only to validate the API key."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB\x82"
    )


def _is_valid_key_response(res, api_key: str) -> bool:
    """Return True only when the response proves the API key is accepted."""
    try:
        data = res.json()
    except Exception as json_err:
        logger.warning(f"[Freeimage] Key probe parse error: {json_err}")
        return False
    if res.status_code == 200 and isinstance(data, dict):
        image = data.get("image")
        status_code = data.get("status_code")
        if isinstance(image, dict) and image.get("url"):
            return True
        if status_code in (200, "200"):
            return True
    logger.warning(
        "[Freeimage] API key rejected: "
        f"status={res.status_code} response={str(data)[:200]} "
        f"key={api_key[:6]}...{api_key[-4:] if len(api_key) > 10 else ''}"
    )
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
                    # HTTP 200 but no URL in response — log raw so we can diagnose API changes
                    logger.warning(
                        f"[Freeimage] HTTP 200 but no viewer URL found in response. "
                        f"image={img_data} raw={res.text[:200]}"
                    )
                except Exception as json_err:
                    logger.warning(f"[Freeimage] JSON parse error: {json_err} (raw text: {res.text[:200]})")
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
