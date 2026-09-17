"""
pixelfed_uploader.py — Pixelfed (Fediverse Photo Sharing) Cross-Poster
======================================================================
Cross-posts high-res anime art/posters to Pixelfed (the decentralized
photo-sharing network / Instagram of the Fediverse).

Each post includes:
  - Attached media (high-res anime poster) with alt text description
  - Rich caption: Title, description, destination/affiliate link, hashtags
  - Visibility: public

Pixelfed utilizes the Mastodon-compatible REST API:
  - Auth: Personal Access Token (Settings → Applications → Personal Access Tokens)
  - Profile check: GET /api/v1/accounts/verify_credentials
  - Media upload:  POST /api/v1/media
  - Status post:   POST /api/v1/statuses
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
    from config import PIXELFED_INSTANCE_URL
    return (PIXELFED_INSTANCE_URL or "https://pixelfed.social").strip().rstrip("/")


def _get_headers() -> dict:
    """Returns auth headers for Pixelfed API calls."""
    from config import PIXELFED_ACCESS_TOKEN
    if not PIXELFED_ACCESS_TOKEN:
        raise ValueError("PIXELFED_ACCESS_TOKEN is not set in .env")
    return {
        "Authorization": f"Bearer {PIXELFED_ACCESS_TOKEN.strip()}",
    }


def verify_pixelfed_token() -> bool:
    """Checks if the PIXELFED_ACCESS_TOKEN is valid by verifying credentials."""
    from config import PIXELFED_ACCESS_TOKEN
    if not PIXELFED_ACCESS_TOKEN:
        return False
    base_url = _get_instance_url()
    try:
        res = requests.get(
            f"{base_url}/api/v1/accounts/verify_credentials",
            headers=_get_headers(),
            timeout=10,
        )
        if res.status_code == 200 and "id" in res.json():
            data = res.json()
            logger.info(f"[Pixelfed] Token valid — logged in as @{data.get('username', '?')}")
            return True
        elif res.status_code == 401:
            logger.warning("[Pixelfed] Token invalid (HTTP 401 Unauthorized).")
            return False
        else:
            logger.warning(f"[Pixelfed] Token verification returned HTTP {res.status_code}")
            return res.status_code != 401
    except Exception as e:
        logger.warning(f"[Pixelfed] Token check failed: {e}")
        return False


def get_pixelfed_profile_info() -> dict | None:
    """
    Fetches Pixelfed account profile details (username, display name, url, followers, posts).
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
            username = data.get("username", "")
            return {
                "username": username,
                "display_name": data.get("display_name", username),
                "url": data.get("url", f"{base_url}/{username}"),
                "followers_count": data.get("followers_count", 0),
                "statuses_count": data.get("statuses_count", data.get("posts_count", 0)),
            }
        else:
            logger.warning(f"[Pixelfed] Profile fetch failed: HTTP {res.status_code}")
    except Exception as e:
        logger.warning(f"[Pixelfed] Could not get profile info: {e}")
    return None


def _clean_tags(title: str) -> str:
    """Generates clean hashtags from title and common anime tags."""
    base_tags = ["#anime", "#animeart", "#wallpaper", "#aesthetic", "#pixelfed"]
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
    Uploads an image to Pixelfed media endpoint and returns media_id.
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
            logger.warning(f"[Pixelfed] Could not read local file {image_path}: {e}")

    if not image_bytes and image_url:
        for ref in [None, "https://www.pinterest.com/"]:
            try:
                headers_dl = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                }
                if ref:
                    headers_dl["Referer"] = ref
                dl_res = requests.get(image_url, headers=headers_dl, timeout=20)
                if dl_res.status_code == 200 and len(dl_res.content) > 100:
                    image_bytes = dl_res.content
                    filename = image_url.split("/")[-1].split("?")[0] or "anime_poster.jpg"
                    break
            except Exception as e:
                logger.warning(f"[Pixelfed] Download attempt error ({image_url}): {e}")

    if not image_bytes:
        logger.error("[Pixelfed] No valid image file or URL available to upload.")
        return None

    # Alt text description (cap at 400 chars)
    alt_text = (description or "Anime Aesthetic Poster")[:400]

    files = {
        "file": (filename, image_bytes, "image/jpeg"),
    }
    data = {
        "description": alt_text,
    }

    try:
        res = requests.post(media_url, headers=headers, files=files, data=data, timeout=35)
        if res.status_code in (200, 201, 202):
            media_id = res.json().get("id")
            logger.info(f"[Pixelfed] Media uploaded successfully: ID={media_id}")
            return str(media_id)
        else:
            logger.error(f"[Pixelfed] Media upload failed: HTTP {res.status_code} — {res.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[Pixelfed] Media upload exception: {e}")
        return None


def post_to_pixelfed(image_url: str, title: str, description: str = "",
                     link: str = "", image_path: str = None) -> str | None:
    """
    Posts a photo status to Pixelfed with attached media, title, caption, link, and hashtags.

    Args:
        image_url:   Public image URL (Catbox / Cloudinary / Pinterest)
        title:       Pin title / headline
        description: Pin caption / description
        link:        Amazon affiliate or destination URL
        image_path:  Optional local path to image file

    Returns:
        Public post URL on success, or None on failure.
    """
    from config import PIXELFED_ENABLED
    if not PIXELFED_ENABLED:
        logger.debug("[Pixelfed] PIXELFED_ENABLED=false -- skipping cross-post.")
        return None

    try:
        headers = _get_headers()
    except ValueError as e:
        logger.error(f"[Pixelfed] Config error: {e}")
        return None

    base_url = _get_instance_url()

    # Step 1: Upload media
    media_id = _upload_media(image_path=image_path, image_url=image_url, description=description or title)
    if not media_id:
        logger.error("[Pixelfed] Media upload failed — cannot create Pixelfed post without photo.")
        return None

    # Step 2: Format status text
    post_text_parts = [title]
    if description:
        clean_desc = description.strip()[:300]
        post_text_parts.append(clean_desc)
    if link:
        if any(k in link.lower() for k in ["amazon", "amzn", "tag=", "/l/"]):
            post_text_parts.append(f"🛍️ Get This Poster / Merch:\n{link}")
        else:
            post_text_parts.append(f"🔗 View & Details:\n{link}")
    hashtags = _clean_tags(title)
    if hashtags:
        post_text_parts.append(hashtags)

    status_text = "\n\n".join(post_text_parts)

    payload = {
        "status": status_text,
        "media_ids": [media_id],
        "visibility": "public",
    }

    # Step 3: POST status with retry
    post_url = f"{base_url}/api/v1/statuses"
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            res = requests.post(post_url, headers=headers, json=payload, timeout=20)
            if res.status_code in (200, 201):
                data = res.json()
                public_url = data.get("url") or f"{base_url}/p/{data.get('id')}"
                logger.info(f"[Pixelfed] Successfully posted: {public_url}")
                return public_url
            else:
                logger.warning(f"[Pixelfed] Attempt {attempt} failed ({res.status_code}): {res.text[:150]}")
        except Exception as e:
            logger.warning(f"[Pixelfed] Attempt {attempt} error: {e}")

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAYS[attempt])

    logger.error("[Pixelfed] All posting attempts failed.")
    return None


# -- Standalone test ----------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Pixelfed Uploader -- Standalone Test")
    print("=" * 60)

    print("\n[1] Verifying token...")
    ok = verify_pixelfed_token()
    print(f"    Token valid: {ok}")

    print("\n[2] Fetching profile info...")
    info = get_pixelfed_profile_info()
    if info:
        print(f"    Username   : @{info['username']}")
        print(f"    Display    : {info['display_name']}")
        print(f"    Followers  : {info['followers_count']}")
        print(f"    Posts      : {info['statuses_count']}")
        print(f"    URL        : {info['url']}")
    else:
        print("    Could not fetch profile info (check PIXELFED_ACCESS_TOKEN in .env).")

    print("\n[3] Test post (dry check):")
    print(f"    Instance   : {_get_instance_url()}")
    print("Done.")
