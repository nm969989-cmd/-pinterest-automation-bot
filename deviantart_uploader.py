"""
deviantart_uploader.py — DeviantArt Cross-Posting Module
=========================================================
Uploads and publishes images directly to your DeviantArt gallery
using the official DeviantArt REST API v1.

Flow:
  1. Downloads image to temporary file (or uses local image_path).
  2. Submits binary file to Sta.sh: POST /api/v1/oauth2/stash/submit
  3. Publishes Sta.sh item to public gallery: POST /api/v1/oauth2/stash/publish
  4. Automatically refreshes access token using refresh_token if expired.

Usage:
    from deviantart_uploader import post_to_deviantart
    ok, url = post_to_deviantart(image_path="...", title="...", description="...")
"""

import os
import time
import tempfile
from typing import Tuple, List, Optional
import requests
from logger import get_logger

logger = get_logger(__name__)

_MAX_RETRIES  = 3
_RETRY_DELAYS = [0, 5, 15]

_cached_access_token = ""


def _get_access_token(force_refresh: bool = False) -> str:
    """
    Returns valid access token, auto-refreshing via refresh_token if needed.
    Checks database bot_metadata first, falls back to config / environment.
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

    # 1. Try reading latest persisted tokens from SQLite bot_metadata
    db_access = ""
    db_refresh = ""
    try:
        from database import get_metadata
        db_access = get_metadata("deviantart_access_token", "").strip()
        db_refresh = get_metadata("deviantart_refresh_token", "").strip()
    except Exception as e:
        logger.debug(f"[DeviantArt] Metadata lookup failed: {e}")

    token = "" if force_refresh else (db_access or DEVIANTART_ACCESS_TOKEN or os.getenv("DEVIANTART_ACCESS_TOKEN", "").strip())
    refresh_tok = db_refresh or DEVIANTART_REFRESH_TOKEN or os.getenv("DEVIANTART_REFRESH_TOKEN", "").strip()

    if not token and refresh_tok and DEVIANTART_CLIENT_ID and DEVIANTART_CLIENT_SECRET:
        token = _refresh_token(DEVIANTART_CLIENT_ID, DEVIANTART_CLIENT_SECRET, refresh_tok)

    if token:
        _cached_access_token = token
    return token


def _refresh_token(client_id: str, client_secret: str, refresh_token: str = "") -> str:
    """
    Refreshes the OAuth2 token, saves rotated tokens to bot_metadata, and returns new access_token.
    """
    global _cached_access_token

    from config import DEVIANTART_REFRESH_TOKEN as ENV_REFRESH

    db_token = ""
    try:
        from database import get_metadata
        db_token = get_metadata("deviantart_refresh_token", "").strip()
    except Exception:
        pass
    env_token = (ENV_REFRESH or os.getenv("DEVIANTART_REFRESH_TOKEN", "")).strip()

    candidates = []
    primary = (refresh_token or db_token or env_token).strip()
    if primary:
        candidates.append(primary)
    for tok in (db_token, env_token):
        if tok and tok not in candidates:
            candidates.append(tok)

    if not candidates:
        logger.error("[DeviantArt] Cannot refresh token: No refresh_token found.")
        return ""

    url = "https://www.deviantart.com/oauth2/token"
    for cand in candidates:
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": cand,
        }
        try:
            r = requests.post(url, data=data, timeout=20)
            if r.status_code == 200:
                resp = r.json()
                new_acc = resp.get("access_token", "")
                new_ref = resp.get("refresh_token", "")
                if new_acc:
                    _cached_access_token = new_acc
                    try:
                        from database import set_metadata
                        set_metadata("deviantart_access_token", new_acc)
                        if new_ref:
                            set_metadata("deviantart_refresh_token", new_ref)
                    except Exception as db_err:
                        logger.warning(f"[DeviantArt] Failed to persist refreshed tokens in DB: {db_err}")
                    logger.info("[DeviantArt] Successfully refreshed OAuth2 access token.")
                    return new_acc
            elif r.status_code == 400 or r.status_code == 401:
                logger.warning(f"[DeviantArt] Refresh token rejected (HTTP {r.status_code}): {r.text[:120]}")
        except Exception as e:
            logger.warning(f"[DeviantArt] Token refresh request error: {e}")

    return ""


def post_to_deviantart(
    image_url: str = "",
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
    link: str = "",
    image_path: str = ""
) -> Tuple[bool, str]:
    """
    Submits an artwork to Sta.sh and publishes it to DeviantArt.
    Returns (success: bool, deviation_url: str).
    """
    from config import (
        DEVIANTART_ENABLED,
        DEVIANTART_CLIENT_ID,
        DEVIANTART_CLIENT_SECRET,
    )

    if not DEVIANTART_ENABLED:
        logger.debug("[DeviantArt] DEVIANTART_ENABLED=false -- skipping post.")
        return False, ""

    if not DEVIANTART_CLIENT_ID or not DEVIANTART_CLIENT_SECRET:
        logger.error("[DeviantArt] Incomplete credentials in config.")
        return False, ""

    if not image_url and not image_path:
        logger.warning("[DeviantArt] No image_url or image_path provided.")
        return False, ""

    access_token = _get_access_token()
    if not access_token:
        logger.error("[DeviantArt] No valid access token available.")
        return False, ""

    # Prepare local image file
    upload_file = ""
    is_temp = False
    if image_path and os.path.exists(image_path) and os.path.isfile(image_path):
        upload_file = image_path
    else:
        target_url = image_url or (image_path if (image_path and str(image_path).startswith(("http://", "https://"))) else "")
        if target_url:
            try:
                logger.info(f"[DeviantArt] Downloading image from {target_url[:60]}...")
                ir = requests.get(target_url, timeout=30)
                if ir.status_code == 200 and len(ir.content) > 1000:
                    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    tmp.write(ir.content)
                    tmp.close()
                    upload_file = tmp.name
                    is_temp = True
                else:
                    logger.error(f"[DeviantArt] Failed to download image: HTTP {ir.status_code}")
                    return False, ""
            except Exception as e:
                logger.error(f"[DeviantArt] Error downloading image: {e}")
                return False, ""

    if not upload_file or not os.path.exists(upload_file):
        return False, ""

    # Prepare caption & tags
    formatted_desc = description.strip()
    if link and link not in formatted_desc:
        formatted_desc += f"<br><br><b>Download 4K Wallpaper & Merch:</b> <a href='{link}'>{link}</a>"

    tags_list = tags or ["anime", "animeart", "wallpaper", "aesthetic", "4k"]
    tags_str = " ".join([t.lstrip("#").lower() for t in tags_list[:15]])

    clean_title = (title or "Anime Aesthetic Wallpaper").split(" - ")[0].split(" | ")[0].strip()[:50]

    stash_url = "https://www.deviantart.com/api/v1/oauth2/stash/submit"
    publish_url = "https://www.deviantart.com/api/v1/oauth2/stash/publish"

    deviation_url = ""
    success = False

    try:
        for attempt in range(1, _MAX_RETRIES + 1):
            delay = _RETRY_DELAYS[attempt - 1]
            if delay:
                time.sleep(delay)

            headers = {"Authorization": f"Bearer {access_token}"}
            stash_data = {
                "title": clean_title,
                "artist_comments": formatted_desc,
                "tags": tags_str,
            }

            try:
                with open(upload_file, "rb") as img_f:
                    files = {"item": (os.path.basename(upload_file), img_f, "image/png")}
                    r = requests.post(stash_url, headers=headers, data=stash_data, files=files, timeout=45)
            except Exception as e:
                logger.warning(f"[DeviantArt] Sta.sh network error (attempt {attempt}): {e}")
                continue

            if r.status_code == 401:
                logger.info("[DeviantArt] Access token expired on Sta.sh upload. Refreshing token...")
                access_token = _refresh_token(DEVIANTART_CLIENT_ID, DEVIANTART_CLIENT_SECRET)
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
                    with open(upload_file, "rb") as img_f:
                        files = {"item": (os.path.basename(upload_file), img_f, "image/png")}
                        r = requests.post(stash_url, headers=headers, data=stash_data, files=files, timeout=45)

            if r.status_code != 200:
                if r.status_code == 429:
                    from circuit_breaker import trip_breaker
                    trip_breaker("deviantart", f"HTTP 429 Rate Limit: {r.text[:80]}", cooldown_hours=6.0)
                logger.warning(f"[DeviantArt] Sta.sh submit failed (attempt {attempt}): HTTP {r.status_code} - {r.text[:120]}")
                continue

            resp_json = r.json()
            itemid = resp_json.get("itemid")
            if not itemid:
                logger.warning(f"[DeviantArt] Sta.sh returned no itemid: {resp_json}")
                continue

            logger.info(f"[DeviantArt] Uploaded to Sta.sh (itemid: {itemid}). Publishing to public gallery...")

            # Step 2: Publish to public gallery
            publish_data = {
                "itemid": itemid,
                "is_mature": 0,
                "agree_submission_policy": 1,
                "agree_tos": 1,
            }

            pr = requests.post(publish_url, headers=headers, data=publish_data, timeout=30)
            if pr.status_code == 401:
                logger.info("[DeviantArt] 401 Unauthorized on publish. Refreshing token...")
                access_token = _refresh_token(DEVIANTART_CLIENT_ID, DEVIANTART_CLIENT_SECRET)
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
                    pr = requests.post(publish_url, headers=headers, data=publish_data, timeout=30)

            if pr.status_code == 200:
                p_json = pr.json()
                deviation_url = p_json.get("url", f"https://www.deviantart.com/muthelyrics (id: {p_json.get('deviationid', '')})")
                logger.info(f"[DeviantArt] Published successfully! URL: {deviation_url}")
                success = True
                break
            else:
                if pr.status_code == 429:
                    from circuit_breaker import trip_breaker
                    trip_breaker("deviantart", f"HTTP 429 Rate Limit: {pr.text[:80]}", cooldown_hours=6.0)
                logger.warning(f"[DeviantArt] Publish failed (attempt {attempt}): HTTP {pr.status_code} - {pr.text[:120]}")

    finally:
        if is_temp and upload_file and os.path.exists(upload_file):
            try:
                os.remove(upload_file)
            except Exception:
                pass

    return success, deviation_url


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

