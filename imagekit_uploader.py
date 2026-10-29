"""
imagekit_uploader.py — ImageKit.io Global CDN & Media Storage Module
===================================================================
Uploads high-resolution anime art, posters, and wallpapers to ImageKit.io.

Features:
  - 20GB free monthly global CDN delivery hosted in Mumbai (India) datacenter
  - High-speed image transformation and responsive thumbnail endpoints
  - Direct upload from local file or remote URL fallback
  - Automatic organization into '/anime_wallpapers/' folder
"""

import os
import io
import time
import requests
from logger import get_logger

logger = get_logger(__name__)

_UPLOAD_URL = "https://upload.imagekit.io/api/v1/files/upload"
_LIST_URL = "https://api.imagekit.io/v1/files"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

_MAX_RETRIES = 3
_RETRY_DELAYS = [0, 4, 12]


def _get_credentials() -> tuple[str, str, str]:
    """Returns (public_key, private_key, url_endpoint)."""
    try:
        from config import IMAGEKIT_PUBLIC_KEY, IMAGEKIT_PRIVATE_KEY, IMAGEKIT_URL_ENDPOINT
        pub = (IMAGEKIT_PUBLIC_KEY or os.getenv("IMAGEKIT_PUBLIC_KEY", "public_rp+18TLVQhsYO9qQnXiMxcTDi2c=")).strip()
        priv = (IMAGEKIT_PRIVATE_KEY or os.getenv("IMAGEKIT_PRIVATE_KEY", "private_PrpAMecPmUx1IQDvZ0bNzg2s+8I=")).strip()
        endpoint = (IMAGEKIT_URL_ENDPOINT or os.getenv("IMAGEKIT_URL_ENDPOINT", "https://ik.imagekit.io/zno3dgy8z")).strip().rstrip("/")
        return pub, priv, endpoint
    except Exception:
        pub = os.getenv("IMAGEKIT_PUBLIC_KEY", "public_rp+18TLVQhsYO9qQnXiMxcTDi2c=").strip()
        priv = os.getenv("IMAGEKIT_PRIVATE_KEY", "private_PrpAMecPmUx1IQDvZ0bNzg2s+8I=").strip()
        endpoint = os.getenv("IMAGEKIT_URL_ENDPOINT", "https://ik.imagekit.io/zno3dgy8z").strip().rstrip("/")
        return pub, priv, endpoint


def verify_imagekit_token() -> bool:
    """Verifies API key validity against ImageKit API without uploading a file."""
    _, private_key, _ = _get_credentials()
    if not private_key:
        return False
    try:
        res = requests.get(
            f"{_LIST_URL}?limit=1",
            auth=(private_key, ""),
            headers=_HEADERS,
            timeout=10
        )
        return res.status_code == 200
    except Exception as e:
        logger.warning(f"[ImageKit] Connectivity check failed: {e}")
        return False


def get_imagekit_info() -> dict:
    """Returns ImageKit service info & configured key status."""
    pub, priv, endpoint = _get_credentials()
    return {
        "service": "ImageKit.io",
        "public_key_masked": f"{pub[:6]}...{pub[-4:]}" if len(pub) > 10 else "configured",
        "private_key_masked": f"{priv[:8]}...{priv[-4:]}" if len(priv) > 12 else "configured",
        "url_endpoint": endpoint,
        "status": "Active" if verify_imagekit_token() else "Reachable",
        "upload_endpoint": _UPLOAD_URL,
    }


def post_to_imagekit(image_path: str,
                     title: str = "",
                     anime_name: str = "",
                     affiliate_url: str = "") -> str | None:
    """
    Uploads an image to ImageKit.io.
    Supports either a local file path or a public image URL.
    Returns the public CDN URL (e.g. 'https://ik.imagekit.io/zno3dgy8z/anime_wallpapers/xyz.jpg')
    on success, or None on failure.
    """
    try:
        from config import IMAGEKIT_ENABLED
        if not IMAGEKIT_ENABLED:
            logger.debug("[ImageKit] IMAGEKIT_ENABLED is False — skipping.")
            return None
    except Exception:
        pass

    _, private_key, endpoint = _get_credentials()
    if not private_key:
        logger.error("[ImageKit] IMAGEKIT_PRIVATE_KEY is not configured.")
        return None

    clean_title = (title or "").strip()
    clean_anime = (anime_name or "").strip()

    # Determine tags
    tags_list = ["anime", "wallpaper"]
    if clean_anime:
        tags_list.append(clean_anime.replace(" ", "_").lower())
    tags_str = ",".join(tags_list)

    # Determine clean filename
    if clean_title:
        base_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in clean_title).strip("_")
        base_name = base_name[:60]
    else:
        base_name = "anime_art"
    target_filename = f"{base_name}.jpg"

    is_local_file = bool(image_path and os.path.exists(image_path) and os.path.isfile(image_path))
    is_url = bool(image_path and image_path.startswith(("http://", "https://")))

    if not is_local_file and not is_url:
        logger.error(f"[ImageKit] Neither local file nor valid URL provided: '{image_path}'")
        return None

    for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
        if delay > 0:
            time.sleep(delay)
        try:
            auth = (private_key, "")
            data = {
                "fileName": target_filename,
                "useUniqueFileName": "true",
                "tags": tags_str,
                "folder": "/anime_wallpapers/",
                "isPrivateFile": "false",
            }

            if is_local_file:
                with open(image_path, "rb") as f:
                    files = {"file": (target_filename, f, "image/jpeg")}
                    response = requests.post(
                        _UPLOAD_URL,
                        data=data,
                        files=files,
                        auth=auth,
                        headers=_HEADERS,
                        timeout=30,
                    )
            else:
                data["file"] = image_path
                response = requests.post(
                    _UPLOAD_URL,
                    data=data,
                    auth=auth,
                    headers=_HEADERS,
                    timeout=30,
                )

            if response.status_code == 200:
                resp_json = response.json()
                cdn_url = resp_json.get("url")
                file_id = resp_json.get("fileId")
                if cdn_url:
                    logger.info(f"[ImageKit] Upload successful (fileId={file_id}): {cdn_url}")
                    return cdn_url

            logger.warning(
                f"[ImageKit] Attempt {attempt}/{_MAX_RETRIES} failed "
                f"(HTTP {response.status_code}): {response.text[:200]}"
            )

        except Exception as exc:
            logger.warning(f"[ImageKit] Attempt {attempt}/{_MAX_RETRIES} error: {exc}")

    logger.error(f"[ImageKit] All {_MAX_RETRIES} upload attempts failed for '{image_path}'.")
    return None

