"""
arena_uploader.py — Are.na Visual Curation Cross-Poster
=========================================================
Posts image blocks to the Are.na channel configured in .env using the Are.na v3 REST API.
Mirrors the pinterest_uploader.py / shutterstock_uploader.py pattern.

Are.na API v3 docs: https://www.are.na/developers/explore
Channel: are.na/manoj-muthelyrics/aesthetic-inspiration-wb-oqfprvnw
Plan: Free (200 blocks free) -> upgrade to Premium ($7/mo) for unlimited

Usage (standalone test):
    python arena_uploader.py
"""

import time
import requests
from logger import get_logger

logger = get_logger(__name__)

# -- API Constants -------------------------------------------------------------
ARENA_API_BASE = "https://api.are.na/v3"
_MAX_RETRIES   = 3
_RETRY_DELAYS  = [0, 5, 15]   # seconds -- mirrors pinterest_uploader pattern


def _get_headers() -> dict:
    """Returns auth headers for Are.na v3 API calls."""
    from config import ARENA_ACCESS_TOKEN
    if not ARENA_ACCESS_TOKEN:
        raise ValueError("ARENA_ACCESS_TOKEN is not set in .env")
    return {
        "Authorization": f"Bearer {ARENA_ACCESS_TOKEN.strip()}",
        "Content-Type":  "application/json",
    }


def post_to_arena(image_url: str, title: str, description: str, link: str = "") -> bool:
    """
    Posts an image block to the Are.na channel (ARENA_CHANNEL_SLUG in .env) using Are.na v3 REST API.

    Args:
        image_url:   Publicly accessible image URL (e.g. from Cloudinary / Catbox).
        title:       Pin title -- set as block title.
        description: Pin caption / description.
        link:        Optional destination URL (Amazon affiliate link, etc.)
                     Passed as original_source_url and appended to description.

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

    # -- Fetch headers first (raises ValueError if token missing) -------------
    try:
        headers = _get_headers()
    except ValueError as e:
        logger.error(f"[Are.na] Config error: {e}")
        return False

    # -- Build payload for Are.na v3 POST /v3/blocks --------------------------
    block_desc = (description or "").strip()
    if link and link not in block_desc:
        block_desc = f"{block_desc}\n\nLink: {link}" if block_desc else f"Link: {link}"

    url = f"{ARENA_API_BASE}/blocks"
    payload = {
        "value": image_url,
        "channel_ids": [ARENA_CHANNEL_SLUG],
    }
    if title:
        payload["title"] = title[:255]
    if block_desc:
        payload["description"] = block_desc[:1000]
    if link:
        payload["original_source_url"] = link

    # -- POST with retry (3 attempts, exponential backoff) --------------------
    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=25)
            if res.status_code in (200, 201):
                block_id = res.json().get("id", "?")
                logger.info(
                    f"[Are.na] Block posted (id={block_id}): '{title[:60]}'"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                )
                return True
            elif res.status_code == 403:
                # 403 = Scope / permission error (token has Read-only instead of Read + Write)
                err_msg = ""
                try:
                    err_msg = res.json().get("details", {}).get("message", res.text[:120])
                except Exception:
                    err_msg = res.text[:120]
                logger.error(
                    f"[Are.na] Permission denied (403): {err_msg} -- "
                    "ARENA_ACCESS_TOKEN must have 'Read + Write' access level."
                )
                try:
                    from circuit_breaker import trip_breaker
                    trip_breaker("arena", f"HTTP 403 Write Scope Missing: {err_msg[:60]}", cooldown_hours=6.0)
                except Exception:
                    pass
                return False   # Token lacks write scope, retry won't fix it
            elif res.status_code == 422:
                # 422 = Unprocessable entity (bad URL, invalid format, etc.)
                logger.warning(
                    f"[Are.na] Block rejected (422): {res.text[:120]} -- "
                    "image URL may be invalid or already posted."
                )
                return False   # No point retrying a 422
            elif res.status_code == 402 or (res.status_code == 403 and "limit" in res.text.lower()):
                # 402 / quota 403 = Paid-plan or free 200-block channel limit reached
                logger.error(
                    f"[Are.na] Quota/plan limit reached (HTTP {res.status_code}): {res.text[:120]} -- "
                    "free plan allows 200 blocks per channel."
                )
                try:
                    from circuit_breaker import trip_breaker
                    trip_breaker("arena", f"HTTP {res.status_code}: Are.na quota exhausted (200-block free limit)", cooldown_hours=24.0)
                except Exception:
                    pass
                return False   # Retrying won't lift the quota any sooner
            elif res.status_code == 429:
                logger.warning(f"[Are.na] Rate limited (HTTP 429): {res.text[:100]}")
                try:
                    from circuit_breaker import trip_breaker
                    trip_breaker("arena", "HTTP 429: Rate limited by Are.na", cooldown_hours=6.0)
                except Exception:
                    pass
                return False
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
    Fetches Are.na channel metadata: title, block count, slug, status, URL.
    Used by /arena Telegram command and doctor health check.

    Returns:
        dict with keys: title, slug, length (block count), status, url
        or None on failure.
    """
    from config import ARENA_CHANNEL_SLUG

    try:
        headers = _get_headers()
        res = requests.get(
            f"{ARENA_API_BASE}/channels/{ARENA_CHANNEL_SLUG}",
            headers=headers,
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            counts = data.get("counts", {})
            block_count = counts.get("blocks", counts.get("contents", 0))
            owner = data.get("owner", {})
            owner_slug = owner.get("slug", "user")
            channel_slug = data.get("slug", ARENA_CHANNEL_SLUG)
            return {
                "title":  data.get("title", ARENA_CHANNEL_SLUG),
                "slug":   channel_slug,
                "length": block_count,
                "status": data.get("visibility", data.get("state", "public")),
                "url":    f"https://www.are.na/{owner_slug}/{channel_slug}",
            }
        else:
            logger.warning(f"[Are.na] Channel info failed: HTTP {res.status_code} - {res.text[:100]}")
    except Exception as e:
        logger.error(f"[Are.na] get_arena_channel_info error: {e}")
    return None


def verify_arena_token() -> bool:
    """
    Checks if the ARENA_ACCESS_TOKEN is valid via /v3/me endpoint.
    Returns True if token can successfully authenticate.
    """
    try:
        headers = _get_headers()
        res = requests.get(
            f"{ARENA_API_BASE}/me",
            headers=headers,
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            logger.info(f"[Are.na] Token valid -- logged in as: {data.get('name', '?')} (@{data.get('slug', '?')})")
            return True
        elif res.status_code == 401:
            logger.warning("[Are.na] Token invalid (HTTP 401 Unauthorized).")
            return False
        else:
            logger.warning(f"[Are.na] Token check returned HTTP {res.status_code}")
            return res.status_code != 401
    except Exception as e:
        logger.error(f"[Are.na] Token verification error: {e}")
        return False


def verify_arena_write_access() -> str:
    """
    Checks the scope level of the ARENA_ACCESS_TOKEN.
    Does a lightweight test POST to detect read-only vs write-capable tokens.

    Returns:
        "write"     — token has write access (can create blocks)
        "read_only" — token is valid but has read-only scope (403 on POST)
        "invalid"   — token is missing or completely invalid (401)
        "unknown"   — could not determine (network error / unexpected response)
    """
    from config import ARENA_CHANNEL_SLUG
    try:
        headers = _get_headers()
    except ValueError:
        return "invalid"

    # First confirm the token is valid at all
    try:
        me_res = requests.get(f"{ARENA_API_BASE}/me", headers=headers, timeout=10)
        if me_res.status_code == 401:
            logger.warning("[Are.na] Write-access check: token invalid (401).")
            return "invalid"
        if me_res.status_code != 200:
            logger.warning(f"[Are.na] Write-access check: unexpected HTTP {me_res.status_code} on /me.")
            return "unknown"
    except Exception as e:
        logger.warning(f"[Are.na] Write-access check network error on /me: {e}")
        return "unknown"

    # Probe write access with an intentionally empty POST — a read-only token
    # gets 403 immediately; a write-capable token gets 422 (empty value) instead.
    try:
        probe_res = requests.post(
            f"{ARENA_API_BASE}/blocks",
            json={"value": "", "channel_ids": [ARENA_CHANNEL_SLUG]},
            headers=headers,
            timeout=10,
        )
        if probe_res.status_code in (200, 201, 422):
            # 422 = "Unprocessable Entity" (write scope confirmed but empty value rejected)
            logger.info("[Are.na] Write-access check: token has READ + WRITE scope. ✓")
            return "write"
        elif probe_res.status_code == 403:
            logger.warning(
                "[Are.na] Write-access check: token is READ-ONLY (HTTP 403). "
                "Regenerate token at https://dev.are.na/ with 'Read + Write' scope."
            )
            return "read_only"
        else:
            logger.warning(f"[Are.na] Write-access check: unexpected HTTP {probe_res.status_code}.")
            return "unknown"
    except Exception as e:
        logger.warning(f"[Are.na] Write-access check network error on POST /blocks: {e}")
        return "unknown"


# -- Standalone test ----------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Are.na Uploader (v3 API) -- Standalone Test")
    print("=" * 60)

    print("\n[1] Verifying token...")
    ok = verify_arena_token()
    print(f"    Token valid: {ok}")

    print("\n[2] Fetching channel info...")
    info = get_arena_channel_info()
    if info:
        print(f"    Channel  : {info['title']}")
        print(f"    Blocks   : {info['length']}/200 used (Free plan)")
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
