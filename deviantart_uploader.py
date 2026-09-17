"""
deviantart_uploader.py — DeviantArt Cross-Posting Module
=========================================================
Uploads and publishes images directly to your DeviantArt gallery
using the official DeviantArt REST API v1.

Flow:
  1. Downloads image to temporary file.
  2. Submits binary file to Sta.sh: POST /api/v1/oauth2/stash/submit
  3. Publishes Sta.sh item to public gallery: POST /api/v1/oauth2/stash/publish
  4. Automatically refreshes access token using refresh_token if expired.

Usage (standalone test):
    python deviantart_uploader.py
"""

import os
import time
import tempfile
import requests
from logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES  = 3
_RETRY_DELAYS = [0, 5, 15]


_cached_access_token = ""


def _get_access_token(force_refresh: bool = False) -> str:
    """
    Returns valid access token, auto-refreshing via refresh_token if needed.
    """
    global _cached_access_token
    from config import (
        DEVIANTART_CLIENT_ID,
        DEVIANTART_CLIENT_SECRET,
        DEVIANTART_ACCESS_TOKEN,
        DEVIANTART_REFRESH_TOKEN,
    )

    if not force_refresh and _cached_access_token:
        return _cached_access_token

    token = "" if force_refresh else DEVIANTART_ACCESS_TOKEN
    if not token and DEVIANTART_REFRESH_TOKEN:
        token = _refresh_token(DEVIANTART_CLIENT_ID, DEVIANTART_CLIENT_SECRET, DEVIANTART_REFRESH_TOKEN)

    if token:
        _cached_access_token = token
    return token


def _save_token_to_env(key: str, value: str):
    """Saves updated OAuth token to .env file so it persists across bot runs."""
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if not os.path.exists(env_path):
            return
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        found = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
                new_lines.append(f"{key}={value}\n")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.warning(f"[DeviantArt] Failed to update {key} in .env: {e}")


def _refresh_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """
    Refreshes the OAuth2 token, saves rotated tokens to .env, and returns new access_token.
    """
    url = "https://www.deviantart.com/oauth2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    try:
        r = requests.post(url, data=payload, timeout=20)
        if r.status_code == 200:
            data = r.json()
            new_access = data.get("access_token", "")
            new_refresh = data.get("refresh_token", "")
            if new_access:
                _save_token_to_env("DEVIANTART_ACCESS_TOKEN", new_access)
            if new_refresh:
                _save_token_to_env("DEVIANTART_REFRESH_TOKEN", new_refresh)
            logger.info("[DeviantArt] Successfully refreshed OAuth2 access token.")
            return new_access
        else:
            logger.error(f"[DeviantArt] Token refresh failed: HTTP {r.status_code} - {r.text}")
    except Exception as e:
        logger.error(f"[DeviantArt] Error during token refresh: {e}")
    return ""


def _download_to_temp(image_url: str) -> str:
    """Downloads image URL to a local temporary file."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(image_url, headers=headers, timeout=20)
        if r.status_code == 200:
            suffix = ".jpg"
            if ".png" in image_url.lower():
                suffix = ".png"
            elif ".webp" in image_url.lower():
                suffix = ".webp"
            
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(r.content)
            tmp.close()
            return tmp.name
        else:
            logger.warning(f"[DeviantArt] Failed to download image: HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"[DeviantArt] Download exception: {e}")
    return None


def post_to_deviantart(image_url: str = "", title: str = "", description: str = "", tags: list = None, link: str = "", image_path: str = "") -> bool:
    """
    Submits and publishes an image to DeviantArt.

    Args:
        image_url:   Public URL of the image (or local path).
        title:       Title of the artwork.
        description: Description / caption.
        tags:        List of tag strings.
        link:        Affiliate / source link to append to artist description.
        image_path:  Optional local path to image file.

    Returns:
        True on success, False otherwise.
    """
    from config import DEVIANTART_ENABLED, DEVIANTART_CLIENT_ID, DEVIANTART_CLIENT_SECRET, DEVIANTART_REFRESH_TOKEN

    if not DEVIANTART_ENABLED:
        logger.debug("[DeviantArt] DEVIANTART_ENABLED=false -- skipping post.")
        return False

    if not image_url and not image_path:
        logger.warning("[DeviantArt] No image_url or image_path provided.")
        return False

    access_token = _get_access_token()
    if not access_token:
        logger.error("[DeviantArt] No valid access token found in .env.")
        return False

    # 1. Resolve local image file
    upload_file = None
    is_temp = False
    if image_path and os.path.exists(image_path):
        upload_file = image_path
    elif image_url and os.path.exists(image_url):
        upload_file = image_url
    elif image_url and (image_url.startswith("http://") or image_url.startswith("https://")):
        upload_file = _download_to_temp(image_url)
        is_temp = True

    if not upload_file or not os.path.exists(upload_file):
        logger.warning("[DeviantArt] Unable to locate or download valid image for submission.")
        return False

    # Format artist description
    full_desc = description or ""
    if link:
        full_desc += f"<br><br>Source / More: <a href='{link}'>{link}</a>"

    tags_list = tags or ["anime", "illustration", "digitalart", "aesthetic", "wallpaper"]

    success = False
    try:
        for attempt, delay in enumerate(_RETRY_DELAYS, start=1):
            if delay > 0:
                time.sleep(delay)

            # Step 1: Submit to Sta.sh
            stash_url = "https://www.deviantart.com/api/v1/oauth2/stash/submit"
            headers = {"Authorization": f"Bearer {access_token}"}
            
            stash_data = {
                "title": title[:100],
                "artist_comments": full_desc,
            }
            for i, tag in enumerate(tags_list[:20]):
                stash_data[f"tags[{i}]"] = tag

            with open(upload_file, "rb") as f:
                files = {"file": (os.path.basename(upload_file), f, "image/jpeg")}
                r = requests.post(stash_url, headers=headers, data=stash_data, files=files, timeout=40)

            # Token expired check (401)
            if r.status_code == 401 and DEVIANTART_REFRESH_TOKEN:
                logger.info("[DeviantArt] 401 Unauthorized — attempting token refresh...")
                access_token = _refresh_token(DEVIANTART_CLIENT_ID, DEVIANTART_CLIENT_SECRET, DEVIANTART_REFRESH_TOKEN)
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
                    continue

            if r.status_code != 200:
                logger.warning(f"[DeviantArt] Sta.sh submit failed (attempt {attempt}): HTTP {r.status_code} - {r.text}")
                continue

            resp_json = r.json()
            itemid = resp_json.get("itemid")
            if not itemid:
                logger.warning(f"[DeviantArt] Sta.sh returned no itemid: {resp_json}")
                continue

            logger.info(f"[DeviantArt] Uploaded to Sta.sh (itemid: {itemid}). Now publishing to gallery...")

            # Step 2: Publish to public gallery
            publish_url = "https://www.deviantart.com/api/v1/oauth2/stash/publish"
            publish_data = {
                "itemid": itemid,
                "agree_submission_policy": 1,
                "agree_tos": 1,
            }

            pr = requests.post(publish_url, headers=headers, data=publish_data, timeout=30)
            if pr.status_code == 200:
                p_json = pr.json()
                dev_url = p_json.get("url", f"https://deviantart.com (id: {p_json.get('deviationid')})")
                logger.info(f"[DeviantArt] Published successfully! URL: {dev_url}")
                success = True
                break
            else:
                logger.warning(f"[DeviantArt] Publish failed (attempt {attempt}): HTTP {pr.status_code} - {pr.text}")

    finally:
        if is_temp and upload_file and os.path.exists(upload_file):
            try:
                os.remove(upload_file)
            except Exception:
                pass

    return success


def verify_deviantart_token() -> bool:
    """Checks if DeviantArt credentials/tokens are valid, auto-refreshing if expired."""
    access_token = _get_access_token()
    if not access_token:
        return False
    try:
        r = requests.get(
            "https://www.deviantart.com/api/v1/oauth2/user/whoami",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return True
        elif r.status_code == 401:
            logger.info("[DeviantArt] Access token expired (HTTP 401), refreshing via refresh_token...")
            new_token = _get_access_token(force_refresh=True)
            if new_token:
                r2 = requests.get(
                    "https://www.deviantart.com/api/v1/oauth2/user/whoami",
                    headers={"Authorization": f"Bearer {new_token}"},
                    timeout=10,
                )
                return r2.status_code == 200
        return False
    except Exception:
        return False


def get_deviantart_user_info() -> dict:
    """Returns username and stats from DeviantArt, auto-refreshing if expired."""
    access_token = _get_access_token()
    if not access_token:
        return {}
    try:
        r = requests.get(
            "https://www.deviantart.com/api/v1/oauth2/user/whoami",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 401:
            new_token = _get_access_token(force_refresh=True)
            if new_token:
                r2 = requests.get(
                    "https://www.deviantart.com/api/v1/oauth2/user/whoami",
                    headers={"Authorization": f"Bearer {new_token}"},
                    timeout=10,
                )
                if r2.status_code == 200:
                    return r2.json()
    except Exception:
        pass
    return {}


if __name__ == "__main__":
    print("Testing DeviantArt Uploader standalone...")
    test_img = "https://images.unsplash.com/photo-1579783902614-a3fb3927b675?w=800"
    res = post_to_deviantart(
        image_url=test_img,
        title="Anime Aesthetic Art Test",
        description="Automated artwork submission test.",
        tags=["anime", "aesthetic", "digitalart"],
        link="https://pinterest.com"
    )
    print(f"Result: {res}")

