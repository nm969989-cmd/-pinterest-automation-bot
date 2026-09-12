"""
arena_uploader.py — Are.na Visual Curation Cross-Poster
=========================================================
Posts image blocks to the Are.na channel configured in .env.
Mirrors the pinterest_uploader.py / shutterstock_uploader.py pattern.

Are.na API v2 docs: https://dev.are.na/documentation
Channel: are.na/manoj-muthelyrics/aesthetic-inspiration
Plan: Guest (200 blocks free) -> upgrade to Premium ($7/mo) for unlimited

Usage (standalone test):
    python arena_uploader.py
"""

import time
import requests
from logger import get_logger

logger = get_logger(__name__)

# -- API Constants -------------------------------------------------------------
ARENA_API_BASE = "https://api.are.na/v2"
_MAX_RETRIES   = 3
_RETRY_DELAYS  = [0, 5, 15]   # seconds -- mirrors pinterest_uploader pattern


def _get_headers() -> dict:
    """Returns auth headers for Are.na API calls."""
    from config import ARENA_ACCESS_TOKEN
    if not ARENA_ACCESS_TOKEN:
        raise ValueError("ARENA_ACCESS_TOKEN is not set in .env")
    return {
        "Authorization": f"Bearer {ARENA_ACCESS_TOKEN}",
        "Content-Type":  "application/json",
    }


def post_to_arena(image_url: str, title: str, description: str, link: str = "") -> bool:
    """
    Posts an image block to the Are.na channel (ARENA_CHANNEL_SLUG in .env).

    Args:
        image_url:   Publicly accessible image URL (e.g. from Cloudinary / Catbox).
        title:       Pin title -- prepended to the block description.
        description: Pin caption / description.
        link:        Optional destination URL (Amazon affiliate link, etc.)
                     Appended to description so it's visible in the block.

    Returns:
        True on success, False on all failures.
    """
    from config import ARENA_ENABLED, ARENA_CHANNEL_SLUG

    # -- Guard: feature toggle ------------------------------------------------
    if not ARENA_ENABLED:
        logger.debug("[Are.na] ARENA_ENABLED=false -- skipping cross-post.")
        return False

    if not image_url:
        logger.warning("[Are.na] No image_url provided -- cannot post block.")
        return False

    # -- Build block description -----------------------------------------------
    # Are.na blocks don't have a separate "title" field for image blocks,
    # so we pack title + description + link into one rich description string.
    block_description_parts = []
    if title:
        block_description_parts.append(f"[{title}]")
    if description:
        block_description_parts.append(description[:400])
    if link:
        block_description_parts.append(f"\nLink: {link}")
    block_description = "\n\n".join(block_description_parts)

    # -- Build request components ---------------------------------------------
    # Fetch headers first (raises ValueError if token missing)
    try:
        headers = _get_headers()
    except ValueError as e:
        logger.error(f"[Are.na] Config error: {e}")
        return False

    # Are.na new API: pass auth_token as BOTH Authorization header AND query param.
    # Bearer-only returns 401 even with a valid personal access token.
    token   = headers["Authorization"].replace("Bearer ", "")
    url     = f"{ARENA_API_BASE}/channels/{ARENA_CHANNEL_SLUG}/blocks"
    params  = {"auth_token": token}
    payload = {
        "source":      image_url,           # Are.na fetches + caches the image
        "description": block_description,   # Shown below the image in channel
    }

    # -- POST with retry (3 attempts, exponential backoff) --------------------
    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(url, json=payload, headers=headers,
                                params=params, timeout=20)
            if res.status_code in (200, 201):
                block_id = res.json().get("id", "?")
                logger.info(
                    f"[Are.na] Block posted (id={block_id}): '{title[:60]}'"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                )
                return True
            elif res.status_code == 422:
                # 422 = Unprocessable entity (bad URL, duplicate, etc.)
                logger.warning(
                    f"[Are.na] Block rejected (422): {res.text[:120]} -- "
                    "image URL may be invalid or already posted."
                )
                return False   # No point retrying a 422
            else:
                logger.warning(
                    f"[Are.na] Attempt {attempt}/{_MAX_RETRIES} failed: "
                    f"HTTP {res.status_code} -- {res.text[:100]}"
                )
        except requests.exceptions.Timeout:
            logger.warning(f"[Are.na] Attempt {attempt}/{_MAX_RETRIES} timed out.")
        except Exception as e:
            logger.warning(f"[Are.na] Attempt {attempt}/{_MAX_RETRIES} exception: {e}")

    logger.error(f"[Are.na] All {_MAX_RETRIES} attempts failed for '{title}'. Skipping.")
    return False


def get_arena_channel_info() -> dict:
    """
    Fetches Are.na channel metadata: title, block count, slug.
    Used by /arena Telegram command and doctor health check.

    Returns:
        dict with keys: title, slug, length (block count), status
        or None on failure.
    """
    from config import ARENA_CHANNEL_SLUG

    try:
        from config import ARENA_CHANNEL_SLUG
        headers = _get_headers()
        token   = headers["Authorization"].replace("Bearer ", "")
        res = requests.get(
            f"{ARENA_API_BASE}/channels/{ARENA_CHANNEL_SLUG}",
            headers=headers,
            params={"auth_token": token},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            return {
                "title":  data.get("title", ARENA_CHANNEL_SLUG),
                "slug":   data.get("slug",  ARENA_CHANNEL_SLUG),
                "length": data.get("length", 0),
                "status": data.get("status", "unknown"),
                "url":    f"https://www.are.na/manoj-muthelyrics/{data.get('slug', ARENA_CHANNEL_SLUG)}",
            }
        else:
            logger.warning(f"[Are.na] Channel info failed: HTTP {res.status_code}")
    except Exception as e:
        logger.error(f"[Are.na] get_arena_channel_info error: {e}")
    return None


def verify_arena_token() -> bool:
    """
    Checks if the ARENA_ACCESS_TOKEN is valid by fetching the channel.
    (The /v2/me endpoint was removed from Are.na's new API — returns 410.)
    Returns True if token can successfully fetch the configured channel.
    """
    try:
        from config import ARENA_CHANNEL_SLUG
        headers = _get_headers()
        token   = headers["Authorization"].replace("Bearer ", "")
        res = requests.get(
            f"{ARENA_API_BASE}/channels/{ARENA_CHANNEL_SLUG}",
            headers=headers,
            params={"auth_token": token},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            logger.info(f"[Are.na] Token valid -- channel: {data.get('title', '?')}")
            return True
        elif res.status_code == 401:
            logger.warning(f"[Are.na] Token invalid (401).")
            return False
        else:
            # 200 not required for token check; any non-401 means token is accepted
            logger.warning(f"[Are.na] Channel check returned HTTP {res.status_code}")
            return res.status_code != 401
    except Exception as e:
        logger.error(f"[Are.na] Token verification error: {e}")
        return False


# -- Standalone test ----------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Are.na Uploader -- Standalone Test")
    print("=" * 60)

    print("\n[1] Verifying token...")
    ok = verify_arena_token()
    print(f"    Token valid: {ok}")

    print("\n[2] Fetching channel info...")
    info = get_arena_channel_info()
    if info:
        print(f"    Channel  : {info['title']}")
        print(f"    Blocks   : {info['length']}/200 used (Guest plan)")
        print(f"    Status   : {info['status']}")
        print(f"    URL      : {info['url']}")
    else:
        print("    Could not fetch channel info.")

    print("\n[3] Posting test block...")
    success = post_to_arena(
        image_url   = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/Biwa_catfish.jpg/640px-Biwa_catfish.jpg",
        title       = "Test Block -- Pinterest Bot",
        description = "This is an automated test block posted by the Pinterest bot Are.na integration.",
        link        = "https://amazon.in",
    )
    print(f"    Post result: {'Success' if success else 'Failed'}")
    print("\nDone.")
