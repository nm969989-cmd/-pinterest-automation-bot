"""
crosspost_dispatcher.py — Unified Multi-Platform Cross-Posting Dispatcher
==========================================================================
Coordinates automated publishing across all 7 social & image hosting platforms:
  1. Are.na
  2. Bluesky
  3. Raindrop.io
  4. Mastodon
  5. Pixelfed
  6. Freeimage.host
  7. Imghippo

Used identically by:
  - Smart Pin Scheduler (automatic time-slot posting)
  - Telegram Control Bot (/post_now and '🚀 Post Next Now' button)

Performance: platform posts run in parallel via ThreadPoolExecutor (~5s vs ~30s sequential).
"""

import os
import time
import random
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from logger import get_logger
from hashtag_optimizer import format_platform_caption, get_platform_tags
from circuit_breaker import is_cooling_down, trip_breaker

logger = get_logger(__name__)

# Maximum workers for parallel crossposting.
# 7 platforms — one thread each. Bounded to avoid overwhelming the network.
_MAX_CROSSPOST_WORKERS = 7


def _check_breaker(platform: str, results: dict) -> bool:
    """Checks if platform is cooling down. If so, records skip status and returns False."""
    cooling, reason, rem = is_cooling_down(platform)
    if cooling:
        logger.info(f"[Crosspost] Skipping {platform.capitalize()} -- Circuit Breaker active ({rem}h left: {reason})")
        results[f"{platform}_ok"] = None
        results[f"{platform}_url"] = f"Cooling down ({rem}h left)"
        return False
    return True


def _handle_platform_error(platform: str, err: object):
    """Detects HTTP 429 rate limits, 403 blocks, 402 out-of-credits, or suspensions and trips circuit breaker."""
    err_str = str(err).lower()
    if any(k in err_str for k in ["429", "too many requests", "rate limit", "ratelimit"]):
        trip_breaker(platform, f"HTTP 429 Rate Limit: {str(err)[:80]}", cooldown_hours=6.0)
    elif any(k in err_str for k in ["403", "forbidden", "suspended", "account terminated", "blocked", "103"]):
        trip_breaker(platform, f"HTTP 403 / Access Blocked: {str(err)[:80]}", cooldown_hours=12.0)
    elif any(k in err_str for k in ["402", "not enough credits", "credit", "quota"]):
        trip_breaker(platform, f"HTTP 402 Quota Exhausted: {str(err)[:80]}", cooldown_hours=24.0)


# ─────────────────────────────────────────────────────────────────────────────
# Individual platform post functions (called in parallel worker threads)
# Each returns a (platform, ok: bool|None, url: str) tuple.
# ─────────────────────────────────────────────────────────────────────────────

def _post_arena(filename, title, description, link, public_img_url, is_stealth, config):
    """Post to Are.na — returns (ok, url)."""
    if not getattr(config, "ARENA_ENABLED", False):
        return None, ""
    results = {}
    if not _check_breaker("arena", results):
        return results.get("arena_ok"), results.get("arena_url", "")
    try:
        from arena_uploader import post_to_arena
        from database import mark_arena_posted, is_arena_posted
        if not is_arena_posted(filename):
            arena_desc = format_platform_caption("general", title, description, link=link, is_stealth=is_stealth)
            ok = post_to_arena(image_url=public_img_url, title=title, description=arena_desc, link=link)
            if ok:
                mark_arena_posted(filename, title=title, image_url=public_img_url)
            slug = getattr(config, "ARENA_CHANNEL_SLUG", "")
            url = f"https://www.are.na/channel/{slug}" if slug else "https://www.are.na/manoj-muthelyrics"
            return bool(ok), url
        slug = getattr(config, "ARENA_CHANNEL_SLUG", "")
        return True, f"https://www.are.na/channel/{slug}" if slug else "https://www.are.na/manoj-muthelyrics"
    except Exception as e:
        logger.warning(f"[Crosspost] Are.na error: {e}")
        _handle_platform_error("arena", e)
        return False, ""


def _post_bluesky(filename, title, description, link, public_img_url, image_path, is_stealth, config):
    """Post to Bluesky — returns (ok, url)."""
    if not getattr(config, "BLUESKY_ENABLED", False):
        return None, ""
    results = {}
    if not _check_breaker("bluesky", results):
        return results.get("bluesky_ok"), results.get("bluesky_url", "")
    try:
        from bluesky_uploader import post_to_bluesky
        from database import mark_bluesky_posted, is_bluesky_posted
        handle = getattr(config, "BLUESKY_HANDLE", "muthelyrics.bsky.social")
        if not is_bluesky_posted(filename):
            time.sleep(random.uniform(1, 3))  # brief jitter; no need for 3-7s in parallel
            bsky_caption = format_platform_caption("bluesky", title, description, link=link, is_stealth=is_stealth)
            post_uri = post_to_bluesky(image_url=public_img_url, title=title, caption=bsky_caption, link=link, image_path=image_path)
            if post_uri:
                mark_bluesky_posted(filename, post_uri=post_uri, image_url=public_img_url)
                rkey = post_uri.split("/")[-1] if "/" in post_uri else ""
                url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else f"https://bsky.app/profile/{handle}"
            else:
                url = f"https://bsky.app/profile/{handle}"
            return bool(post_uri), url
        return True, f"https://bsky.app/profile/{handle}"
    except Exception as e:
        logger.warning(f"[Crosspost] Bluesky error: {e}")
        _handle_platform_error("bluesky", e)
        return False, ""


def _post_raindrop(filename, title, description, link, original_image_url, image_path, config):
    """Post to Raindrop.io — returns (ok, url)."""
    if not getattr(config, "RAINDROP_ENABLED", False):
        return None, ""
    results = {}
    if not _check_breaker("raindrop", results):
        return results.get("raindrop_ok"), results.get("raindrop_url", "")
    try:
        from raindrop_uploader import post_to_raindrop
        from database import mark_raindrop_posted, is_raindrop_posted
        col_id = getattr(config, "RAINDROP_COLLECTION_ID", "")
        if not is_raindrop_posted(filename):
            time.sleep(random.uniform(0.5, 2))
            drop_ok = post_to_raindrop(image_url=original_image_url, title=title, description=description, link=link, image_path=image_path)
            if drop_ok:
                mark_raindrop_posted(filename, title=title, image_url=original_image_url)
            url = f"https://raindrop.io/collection/{col_id}" if col_id else "https://app.raindrop.io"
            return bool(drop_ok), url
        url = f"https://raindrop.io/collection/{col_id}" if col_id else "https://app.raindrop.io"
        return True, url
    except Exception as e:
        logger.warning(f"[Crosspost] Raindrop error: {e}")
        _handle_platform_error("raindrop", e)
        return False, ""


def _post_mastodon(filename, title, description, link, public_img_url, image_path, is_stealth, config):
    """Post to Mastodon — returns (ok, url)."""
    if not getattr(config, "MASTODON_ENABLED", False):
        return None, ""
    results = {}
    if not _check_breaker("mastodon", results):
        return results.get("mastodon_ok"), results.get("mastodon_url", "")
    try:
        from mastodon_uploader import post_to_mastodon
        from database import mark_mastodon_posted, is_mastodon_posted
        if not is_mastodon_posted(filename):
            time.sleep(random.uniform(0.5, 2))
            masto_caption = format_platform_caption("mastodon", title, description, link=link, is_stealth=is_stealth)
            masto_url = post_to_mastodon(image_url=public_img_url, title=title, description=masto_caption, link=link, image_path=image_path)
            if masto_url:
                mark_mastodon_posted(filename, post_url=masto_url, title=title, image_url=public_img_url)
                return True, masto_url
            return False, "https://mastodon.social/@muthelyrics"
        return True, "https://mastodon.social/@muthelyrics"
    except Exception as e:
        logger.warning(f"[Crosspost] Mastodon error: {e}")
        _handle_platform_error("mastodon", e)
        return False, ""


def _post_pixelfed(filename, title, description, link, public_img_url, original_image_url, image_path, is_stealth, config):
    """Post to Pixelfed — returns (ok, url)."""
    if not getattr(config, "PIXELFED_ENABLED", False):
        return None, ""
    results = {}
    if not _check_breaker("pixelfed", results):
        return results.get("pixelfed_ok"), results.get("pixelfed_url", "")
    try:
        from pixelfed_uploader import post_to_pixelfed
        from database import mark_pixelfed_posted, is_pixelfed_posted
        inst_url = getattr(config, "PIXELFED_INSTANCE_URL", "https://pixelfed.social")
        if not is_pixelfed_posted(filename):
            time.sleep(random.uniform(0.5, 2))
            pix_caption = format_platform_caption("pixelfed", title, description, link=link, is_stealth=is_stealth)
            pix_url = post_to_pixelfed(image_url=public_img_url or original_image_url, title=title, description=pix_caption, link=link, image_path=image_path)
            if pix_url:
                mark_pixelfed_posted(filename, post_url=pix_url, title=title, image_url=public_img_url or original_image_url)
                return True, pix_url
            return False, f"{inst_url}/PinterestAutomationBot"
        return True, f"{inst_url}/PinterestAutomationBot"
    except Exception as e:
        logger.warning(f"[Crosspost] Pixelfed error: {e}")
        _handle_platform_error("pixelfed", e)
        return False, ""


def _post_freeimage(filename, title, anime_name, link, public_img_url, original_image_url, image_path, config):
    """Post to Freeimage.host — returns (ok, url)."""
    if not getattr(config, "FREEIMAGE_ENABLED", False):
        return None, ""
    results = {}
    if not _check_breaker("freeimage", results):
        return results.get("freeimage_ok"), results.get("freeimage_url", "")
    try:
        from freeimage_uploader import post_to_freeimage
        from database import mark_freeimage_posted, is_freeimage_posted
        if not is_freeimage_posted(filename):
            fi_url = post_to_freeimage(image_path=image_path, title=title, anime_name=anime_name, affiliate_url=link)
            if fi_url:
                mark_freeimage_posted(filename, post_url=fi_url, title=title, image_url=public_img_url or original_image_url)
                return True, fi_url
            return False, "https://freeimage.host/muthelyrics"
        return True, "https://freeimage.host/muthelyrics"
    except Exception as e:
        logger.warning(f"[Crosspost] Freeimage error: {e}")
        _handle_platform_error("freeimage", e)
        return False, ""


def _post_imghippo(filename, title, anime_name, link, public_img_url, original_image_url, image_path, config):
    """Post to Imghippo — returns (ok, url)."""
    if not getattr(config, "IMGHIPPO_ENABLED", False):
        return None, ""
    results = {}
    if not _check_breaker("imghippo", results):
        return results.get("imghippo_ok"), results.get("imghippo_url", "")
    try:
        from imghippo_uploader import post_to_imghippo
        from database import mark_imghippo_posted, is_imghippo_posted
        if not is_imghippo_posted(filename):
            hip_url = post_to_imghippo(image_path=image_path, title=title, anime_name=anime_name, affiliate_url=link)
            if hip_url:
                mark_imghippo_posted(filename, post_url=hip_url, title=title, image_url=public_img_url or original_image_url)
                return True, hip_url
            return False, "https://www.imghippo.com/dashboard"
        return True, "https://www.imghippo.com/dashboard"
    except Exception as e:
        logger.warning(f"[Crosspost] Imghippo error: {e}")
        _handle_platform_error("imghippo", e)
        return False, ""


# ─────────────────────────────────────────────────────────────────────────────

def dispatch_all_crossposts(pin: dict, image_path: str) -> dict:
    """
    Executes cross-posting to all enabled platforms in PARALLEL using ThreadPoolExecutor.
    CDN upload runs first (sequential prerequisite), then all 7 platform posts fire
    concurrently — reducing total crosspost time from ~30s to ~5s.

    Handles duplicate checking, upload, state recording, and URL generation.
    Returns a dictionary of boolean statuses and live post/profile URLs.
    """
    import config

    filename = os.path.basename(image_path) if image_path else ""
    title = pin.get("title", "")
    description = pin.get("description", "")
    link = pin.get("link", "")
    anime_name = pin.get("anime_name", "")
    original_image_url = pin.get("image_url", "")
    temp_downloaded_file = None

    # ── Download remote image if no local file available ──────────────────────
    if (not image_path or not os.path.exists(image_path)) and original_image_url and original_image_url.startswith("http"):
        try:
            import tempfile
            dl_res = requests.get(original_image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if dl_res.status_code == 200 and len(dl_res.content) > 1000:
                tf = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tf.write(dl_res.content)
                tf.close()
                image_path = tf.name
                temp_downloaded_file = tf.name
                if not filename:
                    filename = os.path.basename(original_image_url.split("?")[0])
                logger.info(f"[Crosspost] Downloaded remote image to local temp file: {image_path}")
        except Exception as dl_e:
            logger.warning(f"[Crosspost] Failed to download remote image for cross-posting: {dl_e}")

    # ── 1. CDN upload (sequential prerequisite — must complete before parallel posts) ──
    public_img_url = original_image_url
    if image_path and os.path.exists(image_path):
        try:
            from image_host import upload_image_to_host
            hosted = upload_image_to_host(image_path)
            if hosted:
                public_img_url = hosted
                logger.info(f"[Crosspost] Permanent public CDN URL: {hosted[:70]}...")
        except Exception as e:
            logger.warning(f"[Crosspost] CDN upload failed (using fallback): {e}")

    # Fallback CDN: Freeimage
    if (not public_img_url or "api.telegram.org" in public_img_url or "pinimg.com" in public_img_url) and image_path and os.path.exists(image_path):
        try:
            from freeimage_uploader import post_to_freeimage
            fi_hosted = post_to_freeimage(image_path=image_path, title=title)
            if fi_hosted:
                public_img_url = fi_hosted
                logger.info(f"[Crosspost] Using Freeimage as public CDN URL: {public_img_url}")
        except Exception:
            pass

    # 3rd-tier CDN fallback: Imghippo
    if (not public_img_url or "api.telegram.org" in public_img_url or "pinimg.com" in public_img_url) and image_path and os.path.exists(image_path):
        try:
            from imghippo_uploader import post_to_imghippo
            hip_hosted = post_to_imghippo(image_path=image_path, title=title, anime_name=anime_name)
            if hip_hosted:
                public_img_url = hip_hosted
                logger.info(f"[Crosspost] Using Imghippo as 3rd-tier CDN URL: {public_img_url}")
        except Exception:
            pass

    if public_img_url and ("api.telegram.org" in public_img_url or "pinimg.com" in public_img_url):
        logger.warning(
            "[Crosspost] WARNING: All CDN uploads failed — public_img_url is a Telegram/Pinterest CDN "
            "that external servers cannot fetch. Are.na, Bluesky, Mastodon posts may fail. "
            "Check Cloudinary/Catbox/Freeimage/Imghippo connectivity."
        )

    # Determine stealth mode (1 in every 3 posts is pure art appreciation without affiliate CTA)
    is_stealth = random.random() < 0.35
    if is_stealth:
        logger.info("[Crosspost] Stealth / Art Appreciation Mode ACTIVE for this cycle")

    results = {
        "arena_ok": None,     "arena_url": "",
        "bluesky_ok": None,   "bluesky_url": "",
        "raindrop_ok": None,  "raindrop_url": "",
        "mastodon_ok": None,  "mastodon_url": "",
        "pixelfed_ok": None,  "pixelfed_url": "",
        "freeimage_ok": None, "freeimage_url": "",
        "imghippo_ok": None,  "imghippo_url": "",
    }

    # ── 2. Parallel platform posts ────────────────────────────────────────────
    # Each platform task runs in its own thread. Failures in one never affect others.
    # The (platform, ok, url) tuples are collected via futures.
    t_start = time.monotonic()

    platform_tasks = {
        "arena":     lambda: _post_arena(filename, title, description, link, public_img_url, is_stealth, config),
        "bluesky":   lambda: _post_bluesky(filename, title, description, link, public_img_url, image_path, is_stealth, config),
        "raindrop":  lambda: _post_raindrop(filename, title, description, link, original_image_url, image_path, config),
        "mastodon":  lambda: _post_mastodon(filename, title, description, link, public_img_url, image_path, is_stealth, config),
        "pixelfed":  lambda: _post_pixelfed(filename, title, description, link, public_img_url, original_image_url, image_path, is_stealth, config),
        "freeimage": lambda: _post_freeimage(filename, title, anime_name, link, public_img_url, original_image_url, image_path, config),
        "imghippo":  lambda: _post_imghippo(filename, title, anime_name, link, public_img_url, original_image_url, image_path, config),
    }

    with ThreadPoolExecutor(max_workers=_MAX_CROSSPOST_WORKERS, thread_name_prefix="crosspost") as executor:
        future_to_platform = {
            executor.submit(fn): platform
            for platform, fn in platform_tasks.items()
        }
        for future in as_completed(future_to_platform, timeout=60):
            platform = future_to_platform[future]
            try:
                ok, url = future.result()
                results[f"{platform}_ok"] = ok
                results[f"{platform}_url"] = url or ""
            except Exception as e:
                logger.error(f"[Crosspost] Unexpected error in {platform} worker: {e}")
                results[f"{platform}_ok"] = False
                results[f"{platform}_url"] = ""

    elapsed = time.monotonic() - t_start
    logger.info(f"[Crosspost] All platform posts completed in {elapsed:.1f}s (parallel)")

    # ── Cleanup temp file ─────────────────────────────────────────────────────
    if temp_downloaded_file and os.path.exists(temp_downloaded_file):
        try:
            os.remove(temp_downloaded_file)
        except Exception:
            pass

    return results
