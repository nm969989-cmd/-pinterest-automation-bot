"""
bluesky_uploader.py — Bluesky (AT Protocol) Visual Feed Cross-Poster
====================================================================
Cross-posts images and aesthetic artwork directly to Bluesky (bsky.app)
using Bluesky's official native REST / XRPC endpoints.

Zero third-party library dependencies required — uses pure requests.
Mirrors the tumblr_uploader.py and arena_uploader.py architecture.

AT Protocol Specs:
  - Base XRPC endpoint: https://bsky.social/xrpc
  - Auth: com.atproto.server.createSession
  - Upload Blob: com.atproto.repo.uploadBlob
  - Create Post: com.atproto.repo.createRecord
  - Max text length: 300 characters / graphemes

Usage (standalone test):
    python bluesky_uploader.py
"""

import os
import re
import time
import datetime
import tempfile
import requests
from logger import get_logger

logger = get_logger(__name__)

BSKY_XRPC_BASE = "https://bsky.social/xrpc"
_MAX_TEXT_LEN  = 300
_RETRY_DELAYS  = [0, 3, 8]

# Session cache: (access_jwt, did, handle, expiry_timestamp)
_SESSION_CACHE = {
    "jwt": "",
    "did": "",
    "handle": "",
    "expires_at": 0,
}


def _get_credentials():
    """Returns verified handle and app password from config."""
    from config import BLUESKY_HANDLE, BLUESKY_APP_PASSWORD
    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        raise ValueError("BLUESKY_HANDLE and BLUESKY_APP_PASSWORD must be set in .env")
    return BLUESKY_HANDLE.strip().lstrip("@"), BLUESKY_APP_PASSWORD.strip()


def _get_session() -> tuple:
    """
    Returns (access_jwt, did, handle) using cached session if still valid,
    or creates a new session via com.atproto.server.createSession.
    """
    now = time.time()
    if _SESSION_CACHE["jwt"] and _SESSION_CACHE["expires_at"] > now + 300:
        return _SESSION_CACHE["jwt"], _SESSION_CACHE["did"], _SESSION_CACHE["handle"]

    handle, password = _get_credentials()
    url = f"{BSKY_XRPC_BASE}/com.atproto.server.createSession"
    payload = {"identifier": handle, "password": password}

    res = requests.post(url, json=payload, timeout=12)
    if res.status_code != 200:
        logger.error(f"[Bluesky] Auth failed (HTTP {res.status_code}): {res.text[:150]}")
        raise ValueError(f"Bluesky authentication failed: HTTP {res.status_code}")

    data = res.json()
    jwt = data.get("accessJwt", "")
    did = data.get("did", "")
    hdl = data.get("handle", handle)

    # JWT tokens typically valid for 2 hours; refresh after 90 minutes
    _SESSION_CACHE["jwt"] = jwt
    _SESSION_CACHE["did"] = did
    _SESSION_CACHE["handle"] = hdl
    _SESSION_CACHE["expires_at"] = now + 5400

    logger.info(f"[Bluesky] Session created successfully for @{hdl} ({did})")
    return jwt, did, hdl


def _extract_facets(text: str) -> list:
    """
    Finds URLs and #hashtags in text and computes byte-level offsets
    required by the AT Protocol Richtext Facet specification.
    """
    facets = []
    text_bytes = text.encode("utf-8")

    # 1. URLs
    url_regex = re.compile(rb"https?://[^\s<>\"'()]+")
    for match in url_regex.finditer(text_bytes):
        url = match.group(0).decode("utf-8")
        facets.append({
            "index": {
                "byteStart": match.start(),
                "byteEnd": match.end()
            },
            "features": [{
                "$type": "app.bsky.richtext.facet#link",
                "uri": url
            }]
        })

    # 2. Hashtags
    tag_regex = re.compile(rb"#([a-zA-Z0-9_]+)")
    for match in tag_regex.finditer(text_bytes):
        tag_name = match.group(1).decode("utf-8")
        facets.append({
            "index": {
                "byteStart": match.start(),
                "byteEnd": match.end()
            },
            "features": [{
                "$type": "app.bsky.richtext.facet#tag",
                "tag": tag_name
            }]
        })

    return facets


def _download_image(image_url: str) -> str:
    """Downloads an image URL to a local temp file. Returns temp file path or None."""
    try:
        r = requests.get(
            image_url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 Pinterest-Bot-Bluesky/1.0"}
        )
        if r.status_code == 200 and len(r.content) > 100:
            suffix = ".jpg"
            ct = r.headers.get("content-type", "").lower()
            if "png" in ct: suffix = ".png"
            elif "webp" in ct: suffix = ".webp"
            
            tmp = tempfile.mktemp(suffix=suffix)
            with open(tmp, "wb") as f:
                f.write(r.content)
            return tmp
        else:
            logger.warning(f"[Bluesky] Image download failed: HTTP {r.status_code}")
            return None
    except Exception as e:
        logger.warning(f"[Bluesky] Image download error: {e}")
        return None


def _upload_blob(image_path: str, access_jwt: str) -> dict:
    """Uploads binary image to Bluesky via com.atproto.repo.uploadBlob."""
    mime_type = "image/jpeg"
    ext = os.path.splitext(image_path)[1].lower()
    if ext == ".png": mime_type = "image/png"
    elif ext == ".webp": mime_type = "image/webp"

    with open(image_path, "rb") as f:
        data = f.read()

    # Bluesky blob limit is 1,000,000 bytes (1MB). If larger, compress down.
    if len(data) > 950_000:
        try:
            from PIL import Image
            import io
            im = Image.open(image_path)
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            out = io.BytesIO()
            quality = 85
            im.save(out, format="JPEG", quality=quality, optimize=True)
            while out.tell() > 950_000 and quality > 30:
                quality -= 10
                out = io.BytesIO()
                im.save(out, format="JPEG", quality=quality, optimize=True)
            data = out.getvalue()
            mime_type = "image/jpeg"
        except Exception as e:
            logger.warning(f"[Bluesky] Image compression warning: {e}")

    url = f"{BSKY_XRPC_BASE}/com.atproto.repo.uploadBlob"
    headers = {
        "Authorization": f"Bearer {access_jwt}",
        "Content-Type": mime_type,
    }
    res = requests.post(url, data=data, headers=headers, timeout=20)
    if res.status_code == 200:
        return res.json().get("blob")
    else:
        logger.error(f"[Bluesky] Blob upload failed (HTTP {res.status_code}): {res.text[:150]}")
        return None


def post_to_bluesky(image_url: str = "", title: str = "", caption: str = "",
                    link: str = "", tags: list = None, image_path: str = None) -> str:
    """
    Posts an image with rich text, alt text, and links to Bluesky.

    Args:
        image_url:   Public image URL.
        title:       Title of the artwork / pin.
        caption:     Optional description.
        link:        Affiliate / destination link.
        tags:        List of hashtag strings (e.g. ['anime', 'art']).
        image_path:  Direct local file path (preferred for zero-download uploads).

    Returns:
        post_uri (str) on success, or None on failure.
    """
    from config import BLUESKY_ENABLED
    if not BLUESKY_ENABLED:
        logger.debug("[Bluesky] BLUESKY_ENABLED=false -- skipping.")
        return None

    # Resolve local file
    cleanup_tmp = False
    actual_path = image_path
    if not actual_path or not os.path.exists(actual_path):
        if image_url:
            actual_path = _download_image(image_url)
            cleanup_tmp = True

    if not actual_path or not os.path.exists(actual_path):
        logger.warning("[Bluesky] No valid image file found to post.")
        return None

    # Construct rich text within 300 chars limit
    # Structure: Title \n Link \n #tags
    tag_str = ""
    if tags:
        clean_tags = [f"#{t.lstrip('#')}" for t in tags[:5] if t]
        tag_str = " ".join(clean_tags)
    elif "#" in caption:
        # Extract existing tags from caption
        found = re.findall(r"#\w+", caption)
        if found:
            tag_str = " ".join(found[:5])

    text_parts = []
    clean_title = (title or "").strip()
    if clean_title:
        text_parts.append(clean_title)

    if link and link.startswith("http"):
        text_parts.append(f"\U0001f6d2 Merch: {link}")

    if tag_str:
        text_parts.append(tag_str)

    full_text = "\n\n".join(text_parts).strip()
    if len(full_text) > _MAX_TEXT_LEN:
        # Truncate title to fit
        excess = len(full_text) - _MAX_TEXT_LEN + 4
        clean_title = clean_title[:-excess] + "..."
        text_parts[0] = clean_title
        full_text = "\n\n".join(text_parts).strip()

    facets = _extract_facets(full_text)

    # Post with retries
    try:
        for attempt, delay in enumerate(_RETRY_DELAYS, 1):
            if delay > 0:
                time.sleep(delay)
            try:
                jwt, did, handle = _get_session()
                blob = _upload_blob(actual_path, jwt)
                if not blob:
                    continue

                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                record = {
                    "$type": "app.bsky.feed.post",
                    "text": full_text,
                    "createdAt": now_iso,
                    "embed": {
                        "$type": "app.bsky.embed.images",
                        "images": [
                            {
                                "alt": clean_title or "Aesthetic Anime Art",
                                "image": blob
                            }
                        ]
                    }
                }
                if facets:
                    record["facets"] = facets

                create_url = f"{BSKY_XRPC_BASE}/com.atproto.repo.createRecord"
                headers = {
                    "Authorization": f"Bearer {jwt}",
                    "Content-Type": "application/json"
                }
                res = requests.post(
                    create_url,
                    json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
                    headers=headers,
                    timeout=15
                )

                if res.status_code == 200:
                    resp_data = res.json()
                    post_uri = resp_data.get("uri", "")
                    post_cid = resp_data.get("cid", "")
                    logger.info(f"[Bluesky] Post created successfully! URI: {post_uri}")
                    return post_uri
                elif res.status_code == 401:
                    # Clear session cache and retry
                    _SESSION_CACHE["jwt"] = ""
                    _SESSION_CACHE["expires_at"] = 0
                    logger.warning("[Bluesky] Session expired, refreshing...")
                else:
                    logger.warning(f"[Bluesky] createRecord attempt {attempt} failed: HTTP {res.status_code} {res.text[:120]}")
            except Exception as e:
                logger.warning(f"[Bluesky] Attempt {attempt} error: {e}")

        return None
    finally:
        if cleanup_tmp and actual_path and os.path.exists(actual_path):
            try:
                os.remove(actual_path)
            except Exception:
                pass


def verify_bluesky_credentials() -> bool:
    """Verifies that BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are valid."""
    try:
        jwt, did, handle = _get_session()
        return bool(jwt and did)
    except Exception as e:
        logger.warning(f"[Bluesky] Verification failed: {e}")
        return False


def get_bluesky_profile_info() -> dict:
    """
    Fetches the connected Bluesky profile details (handle, display name, followers, post count).
    """
    try:
        jwt, did, handle = _get_session()
        url = f"{BSKY_XRPC_BASE}/app.bsky.actor.getProfile"
        headers = {"Authorization": f"Bearer {jwt}"}
        res = requests.get(url, params={"actor": handle}, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return {
                "handle":        data.get("handle", handle),
                "display_name":  data.get("displayName", handle),
                "followers":     data.get("followersCount", 0),
                "follows":       data.get("followsCount", 0),
                "posts":         data.get("postsCount", 0),
                "description":   data.get("description", ""),
                "url":           f"https://bsky.app/profile/{data.get('handle', handle)}",
            }
        else:
            logger.warning(f"[Bluesky] getProfile returned HTTP {res.status_code}")
    except Exception as e:
        logger.error(f"[Bluesky] get_bluesky_profile_info error: {e}")
    return None


# ── Standalone CLI Test ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  Bluesky Uploader — Standalone Test")
    print("=" * 60)

    print("\n[1] Verifying credentials...")
    ok = verify_bluesky_credentials()
    print(f"    Valid: {ok}")

    if ok:
        print("\n[2] Fetching profile info...")
        profile = get_bluesky_profile_info()
        if profile:
            print(f"    Handle:      @{profile['handle']}")
            print(f"    Name:        {profile['display_name']}")
            print(f"    Followers:   {profile['followers']}")
            print(f"    Posts:       {profile['posts']}")
            print(f"    URL:         {profile['url']}")

    print("\nDone.")
