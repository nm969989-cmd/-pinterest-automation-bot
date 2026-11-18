"""
diigo_uploader.py — Diigo Social Bookmarking Cross-Posting Module
================================================================
Saves public anime wallpaper + merch bookmarks to Diigo via API v2
for high-DA SEO backlinks and search discovery.

Usage:
    from diigo_uploader import post_to_diigo, verify_diigo_credentials
    ok, url = post_to_diigo(url="https://...", title="...", description="...", tags=[...])
"""

import time
from typing import Tuple, List, Optional
import requests
from logger import get_logger

logger = get_logger(__name__)

_DIIGO_API_URL = "https://secure.diigo.com/api/v2/bookmarks"
_MAX_RETRIES = 2


def verify_diigo_credentials() -> Tuple[bool, str]:
    """Verifies Diigo API v2 credentials. Returns (ok, username_or_error)."""
    from config import DIIGO_USERNAME, DIIGO_PASSWORD, DIIGO_API_KEY
    if not (DIIGO_USERNAME and DIIGO_PASSWORD and DIIGO_API_KEY):
        return False, "Missing DIIGO_USERNAME, DIIGO_PASSWORD, or DIIGO_API_KEY"
    try:
        r = requests.get(
            _DIIGO_API_URL,
            auth=(DIIGO_USERNAME, DIIGO_PASSWORD),
            params={"key": DIIGO_API_KEY, "user": DIIGO_USERNAME, "count": 1},
            timeout=12,
        )
        if r.status_code == 200:
            return True, DIIGO_USERNAME
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, str(e)


def post_to_diigo(
    url: str,
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Creates a public bookmark on Diigo via API v2.
    Returns (success: bool, profile_url: str).
    """
    from config import DIIGO_ENABLED, DIIGO_USERNAME, DIIGO_PASSWORD, DIIGO_API_KEY

    if not DIIGO_ENABLED:
        logger.debug("[Diigo] DIIGO_ENABLED=false -- skipping.")
        return False, ""

    if not (DIIGO_USERNAME and DIIGO_PASSWORD and DIIGO_API_KEY):
        logger.error("[Diigo] Missing DIIGO_USERNAME, DIIGO_PASSWORD, or DIIGO_API_KEY.")
        return False, ""

    if not url or not url.startswith(("http://", "https://")):
        logger.warning("[Diigo] Valid HTTP(S) url required.")
        return False, ""

    clean_tags = [t.lstrip("#").lower().replace(" ", "") for t in (tags or ["anime", "wallpaper", "4k", "aesthetic"]) if t.strip()]
    tags_str = ",".join(clean_tags[:10])
    profile_url = f"https://www.diigo.com/user/{DIIGO_USERNAME}"

    payload = {
        "key": DIIGO_API_KEY,
        "title": (title or "4K Anime Wallpaper & Merch").strip()[:250],
        "url": url.strip(),
        "shared": "yes",
        "tags": tags_str,
        "desc": (description or title or "").strip()[:500],
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = requests.post(
                _DIIGO_API_URL,
                auth=(DIIGO_USERNAME, DIIGO_PASSWORD),
                data=payload,
                timeout=20,
            )
            if r.status_code in (200, 201):
                logger.info(f"[Diigo] Bookmark saved for '{payload['title'][:40]}'")
                return True, profile_url
            if r.status_code == 429:
                from circuit_breaker import trip_breaker
                trip_breaker("diigo", f"HTTP 429 Rate Limit: {r.text[:80]}", cooldown_hours=6.0)
                return False, profile_url
            if r.status_code in (401, 403):
                logger.warning(f"[Diigo] Auth rejected (HTTP {r.status_code}): {r.text[:120]}")
                return False, profile_url
            logger.warning(f"[Diigo] Save failed (attempt {attempt}): HTTP {r.status_code} - {r.text[:120]}")
        except Exception as e:
            logger.warning(f"[Diigo] Network error (attempt {attempt}): {e}")
        if attempt < _MAX_RETRIES:
            time.sleep(2)

    return False, profile_url
