"""
crosspost_dispatcher.py — Unified Multi-Platform Cross-Posting Dispatcher
==========================================================================
Coordinates automated publishing across all 10 social & image hosting platforms:
  1. Are.na
  2. Tumblr
  3. Bluesky
  4. Raindrop.io
  5. Mastodon
  6. DeviantArt
  7. Pixelfed
  8. Freeimage.host
  9. ImgBB
  10. Imghippo

Used identically by:
  - Smart Pin Scheduler (automatic time-slot posting)
  - Telegram Control Bot (/post_now and '🚀 Post Next Now' button)
"""

import os
import time
import random
import requests
from logger import get_logger
from hashtag_optimizer import format_platform_caption, get_platform_tags
from circuit_breaker import is_cooling_down, trip_breaker

logger = get_logger(__name__)


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


def dispatch_all_crossposts(pin: dict, image_path: str) -> dict:
    """
    Executes cross-posting to all enabled platforms in sequence.
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
    if (not image_path or not os.path.exists(image_path)) and original_image_url and original_image_url.startswith("http"):
        try:
            import tempfile, requests
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

    # 1. Obtain public CDN URL (essential because Pinterest / Telegram CDNs block server fetches)
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

    # Fallback: if public_img_url is still empty or a blocked domain, upload to Freeimage early
    if (not public_img_url or "api.telegram.org" in public_img_url or "pinimg.com" in public_img_url) and image_path and os.path.exists(image_path):
        try:
            from freeimage_uploader import post_to_freeimage
            fi_hosted = post_to_freeimage(image_path=image_path, title=title)
            if fi_hosted:
                public_img_url = fi_hosted
                logger.info(f"[Crosspost] Using Freeimage as public CDN URL: {public_img_url}")
        except Exception:
            pass

    results = {
        "arena_ok": None, "arena_url": "",
        "tumblr_ok": None, "tumblr_url": "",
        "bluesky_ok": None, "bluesky_url": "",
        "raindrop_ok": None, "raindrop_url": "",
        "mastodon_ok": None, "mastodon_url": "",
        "deviantart_ok": None, "deviantart_url": "",
        "pixelfed_ok": None, "pixelfed_url": "",
        "freeimage_ok": None, "freeimage_url": "",
        "imgbb_ok": None, "imgbb_url": "",
        "imghippo_ok": None, "imghippo_url": "",
    }

    # Determine stealth mode (1 in every 3 posts is pure community art appreciation without affiliate CTA)
    is_stealth = random.random() < 0.35
    if is_stealth:
        logger.info("[Crosspost] Stealth / Art Appreciation Mode ACTIVE for this cycle (omitting commercial CTA on social feeds)")

    # ── 1. Are.na ────────────────────────────────────────────────────────────
    if getattr(config, "ARENA_ENABLED", False) and _check_breaker("arena", results):
        try:
            from arena_uploader import post_to_arena
            from database import mark_arena_posted, is_arena_posted
            if not is_arena_posted(filename):
                arena_desc = format_platform_caption("general", title, description, link=link, anime_name=anime_name, is_stealth=is_stealth)
                ok = post_to_arena(
                    image_url=public_img_url,
                    title=title,
                    description=arena_desc,
                    link=link,
                )
                results["arena_ok"] = bool(ok)
                if ok:
                    mark_arena_posted(filename, title=title, image_url=public_img_url)
            else:
                results["arena_ok"] = True
            slug = getattr(config, "ARENA_CHANNEL_SLUG", "")
            results["arena_url"] = f"https://www.are.na/channel/{slug}" if slug else "https://www.are.na/manoj-muthelyrics"
        except Exception as e:
            results["arena_ok"] = False
            logger.warning(f"[Crosspost] Are.na error: {e}")
            _handle_platform_error("arena", e)

    # ── 2. Tumblr ────────────────────────────────────────────────────────────
    if getattr(config, "TUMBLR_ENABLED", False) and _check_breaker("tumblr", results):
        try:
            from tumblr_uploader import post_to_tumblr
            from database import mark_tumblr_posted, is_tumblr_posted
            blog = getattr(config, "TUMBLR_BLOG_NAME", "animeasthet07")
            if not is_tumblr_posted(filename):
                time.sleep(random.uniform(2, 5))
                post_id = post_to_tumblr(
                    image_url=original_image_url,
                    title=title,
                    caption=description,
                    link=link,
                    image_path=image_path,
                )
                results["tumblr_ok"] = bool(post_id)
                if post_id:
                    mark_tumblr_posted(filename, post_id=str(post_id), blog=blog, image_url=original_image_url)
                    results["tumblr_url"] = f"https://{blog}.tumblr.com/post/{post_id}"
                else:
                    results["tumblr_url"] = f"https://{blog}.tumblr.com"
            else:
                results["tumblr_ok"] = True
                results["tumblr_url"] = f"https://{blog}.tumblr.com"
        except Exception as e:
            results["tumblr_ok"] = False
            logger.warning(f"[Crosspost] Tumblr error: {e}")
            _handle_platform_error("tumblr", e)

    # ── 3. Bluesky ───────────────────────────────────────────────────────────
    if getattr(config, "BLUESKY_ENABLED", False) and _check_breaker("bluesky", results):
        try:
            from bluesky_uploader import post_to_bluesky
            from database import mark_bluesky_posted, is_bluesky_posted
            handle = getattr(config, "BLUESKY_HANDLE", "muthelyrics.bsky.social")
            if not is_bluesky_posted(filename):
                time.sleep(random.uniform(3, 7))
                bsky_caption = format_platform_caption("bluesky", title, description, link=link, anime_name=anime_name, is_stealth=is_stealth)
                post_uri = post_to_bluesky(
                    image_url=public_img_url,
                    title=title,
                    caption=bsky_caption,
                    link=link,
                    image_path=image_path,
                )
                results["bluesky_ok"] = bool(post_uri)
                if post_uri:
                    mark_bluesky_posted(filename, post_uri=post_uri, image_url=public_img_url)
                    rkey = post_uri.split("/")[-1] if "/" in post_uri else ""
                    results["bluesky_url"] = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else f"https://bsky.app/profile/{handle}"
                else:
                    results["bluesky_url"] = f"https://bsky.app/profile/{handle}"
            else:
                results["bluesky_ok"] = True
                results["bluesky_url"] = f"https://bsky.app/profile/{handle}"
        except Exception as e:
            results["bluesky_ok"] = False
            logger.warning(f"[Crosspost] Bluesky error: {e}")
            _handle_platform_error("bluesky", e)

    # ── 4. Raindrop.io ───────────────────────────────────────────────────────
    if getattr(config, "RAINDROP_ENABLED", False) and _check_breaker("raindrop", results):
        try:
            from raindrop_uploader import post_to_raindrop
            from database import mark_raindrop_posted, is_raindrop_posted
            col_id = getattr(config, "RAINDROP_COLLECTION_ID", "")
            if not is_raindrop_posted(filename):
                time.sleep(random.uniform(2, 5))
                drop_ok = post_to_raindrop(
                    image_url=original_image_url,
                    title=title,
                    description=description,
                    link=link,
                    image_path=image_path,
                )
                results["raindrop_ok"] = bool(drop_ok)
                if drop_ok:
                    mark_raindrop_posted(filename, title=title, image_url=original_image_url)
            else:
                results["raindrop_ok"] = True
            results["raindrop_url"] = f"https://raindrop.io/collection/{col_id}" if col_id else "https://app.raindrop.io"
        except Exception as e:
            results["raindrop_ok"] = False
            logger.warning(f"[Crosspost] Raindrop error: {e}")
            _handle_platform_error("raindrop", e)

    # ── 5. Mastodon ──────────────────────────────────────────────────────────
    if getattr(config, "MASTODON_ENABLED", False) and _check_breaker("mastodon", results):
        try:
            from mastodon_uploader import post_to_mastodon
            from database import mark_mastodon_posted, is_mastodon_posted
            if not is_mastodon_posted(filename):
                time.sleep(random.uniform(3, 8))
                masto_caption = format_platform_caption("mastodon", title, description, link=link, anime_name=anime_name, is_stealth=is_stealth)
                masto_url = post_to_mastodon(
                    image_url=public_img_url,
                    title=title,
                    description=masto_caption,
                    link=link,
                    image_path=image_path,
                )
                results["mastodon_ok"] = bool(masto_url)
                if masto_url:
                    mark_mastodon_posted(filename, post_url=masto_url, title=title, image_url=public_img_url)
                    results["mastodon_url"] = masto_url
                else:
                    results["mastodon_url"] = "https://mastodon.social/@muthelyrics"
            else:
                results["mastodon_ok"] = True
                results["mastodon_url"] = "https://mastodon.social/@muthelyrics"
        except Exception as e:
            results["mastodon_ok"] = False
            logger.warning(f"[Crosspost] Mastodon error: {e}")
            _handle_platform_error("mastodon", e)

    # ── 6. DeviantArt ────────────────────────────────────────────────────────
    if getattr(config, "DEVIANTART_ENABLED", False) and _check_breaker("deviantart", results):
        try:
            from deviantart_uploader import post_to_deviantart
            from database import mark_deviantart_posted, is_deviantart_posted
            if not is_deviantart_posted(filename):
                time.sleep(random.uniform(3, 8))
                da_desc = format_platform_caption("deviantart", title, description, link=link, anime_name=anime_name, is_stealth=is_stealth)
                da_tags = get_platform_tags(anime_name, platform="deviantart")
                da_ok = post_to_deviantart(
                    image_url=public_img_url or original_image_url,
                    title=title,
                    description=da_desc,
                    tags=da_tags,
                    link=link,
                    image_path=image_path,
                )
                results["deviantart_ok"] = bool(da_ok)
                if da_ok:
                    mark_deviantart_posted(filename, title=title, image_url=public_img_url or original_image_url)
            else:
                results["deviantart_ok"] = True
            results["deviantart_url"] = "https://www.deviantart.com/muthelyrics"
        except Exception as e:
            results["deviantart_ok"] = False
            logger.warning(f"[Crosspost] DeviantArt error: {e}")
            _handle_platform_error("deviantart", e)

    # ── 7. Pixelfed ──────────────────────────────────────────────────────────
    if getattr(config, "PIXELFED_ENABLED", False) and _check_breaker("pixelfed", results):
        try:
            from pixelfed_uploader import post_to_pixelfed
            from database import mark_pixelfed_posted, is_pixelfed_posted
            inst_url = getattr(config, "PIXELFED_INSTANCE_URL", "https://pixelfed.social")
            if not is_pixelfed_posted(filename):
                time.sleep(random.uniform(3, 8))
                pix_caption = format_platform_caption("pixelfed", title, description, link=link, anime_name=anime_name, is_stealth=is_stealth)
                pix_url = post_to_pixelfed(
                    image_url=public_img_url or original_image_url,
                    title=title,
                    description=pix_caption,
                    link=link,
                    image_path=image_path,
                )
                results["pixelfed_ok"] = bool(pix_url)
                if pix_url:
                    mark_pixelfed_posted(filename, post_url=pix_url, title=title, image_url=public_img_url or original_image_url)
                    results["pixelfed_url"] = pix_url
                else:
                    results["pixelfed_url"] = f"{inst_url}/PinterestAutomationBot"
            else:
                results["pixelfed_ok"] = True
                results["pixelfed_url"] = f"{inst_url}/PinterestAutomationBot"
        except Exception as e:
            results["pixelfed_ok"] = False
            logger.warning(f"[Crosspost] Pixelfed error: {e}")
            _handle_platform_error("pixelfed", e)

    # ── 8. Freeimage.host ────────────────────────────────────────────────────
    if getattr(config, "FREEIMAGE_ENABLED", False) and _check_breaker("freeimage", results):
        try:
            from freeimage_uploader import post_to_freeimage
            from database import mark_freeimage_posted, is_freeimage_posted
            if not is_freeimage_posted(filename):
                fi_url = post_to_freeimage(
                    image_path=image_path,
                    title=title,
                    anime_name=anime_name,
                    affiliate_url=link,
                )
                results["freeimage_ok"] = bool(fi_url)
                if fi_url:
                    mark_freeimage_posted(filename, post_url=fi_url, title=title, image_url=public_img_url or original_image_url)
                    results["freeimage_url"] = fi_url
                else:
                    results["freeimage_url"] = "https://freeimage.host/muthelyrics"
            else:
                results["freeimage_ok"] = True
                results["freeimage_url"] = "https://freeimage.host/muthelyrics"
        except Exception as e:
            results["freeimage_ok"] = False
            logger.warning(f"[Crosspost] Freeimage error: {e}")
            _handle_platform_error("freeimage", e)

    # ── 9. ImgBB ─────────────────────────────────────────────────────────────
    if getattr(config, "IMGBB_ENABLED", False) and _check_breaker("imgbb", results):
        try:
            from imgbb_uploader import post_to_imgbb
            from database import mark_imgbb_posted, is_imgbb_posted
            if not is_imgbb_posted(filename):
                ibb_url = post_to_imgbb(
                    image_path=image_path,
                    title=title,
                    anime_name=anime_name,
                    affiliate_url=link,
                )
                results["imgbb_ok"] = bool(ibb_url)
                if ibb_url:
                    mark_imgbb_posted(filename, post_url=ibb_url, title=title, image_url=public_img_url or original_image_url)
                    results["imgbb_url"] = ibb_url
                else:
                    results["imgbb_url"] = "https://muthelyrics.imgbb.com/"
            else:
                results["imgbb_ok"] = True
                results["imgbb_url"] = "https://muthelyrics.imgbb.com/"
        except Exception as e:
            results["imgbb_ok"] = False
            logger.warning(f"[Crosspost] ImgBB error: {e}")
            _handle_platform_error("imgbb", e)

    # ── 10. Imghippo ─────────────────────────────────────────────────────────
    if getattr(config, "IMGHIPPO_ENABLED", False) and _check_breaker("imghippo", results):
        try:
            from imghippo_uploader import post_to_imghippo
            from database import mark_imghippo_posted, is_imghippo_posted
            if not is_imghippo_posted(filename):
                hip_url = post_to_imghippo(
                    image_path=image_path,
                    title=title,
                    anime_name=anime_name,
                    affiliate_url=link,
                )
                results["imghippo_ok"] = bool(hip_url)
                if hip_url:
                    mark_imghippo_posted(filename, post_url=hip_url, title=title, image_url=public_img_url or original_image_url)
                    results["imghippo_url"] = hip_url
                else:
                    results["imghippo_url"] = "https://www.imghippo.com/dashboard"
            else:
                results["imghippo_ok"] = True
                results["imghippo_url"] = "https://www.imghippo.com/dashboard"
        except Exception as e:
            results["imghippo_ok"] = False
            logger.warning(f"[Crosspost] Imghippo error: {e}")
            _handle_platform_error("imghippo", e)

    if temp_downloaded_file and os.path.exists(temp_downloaded_file):
        try:
            os.remove(temp_downloaded_file)
        except Exception:
            pass

    return results
