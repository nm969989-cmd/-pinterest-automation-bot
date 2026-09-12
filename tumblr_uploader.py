"""
tumblr_uploader.py - Tumblr cross-posting module for the Pinterest Bot.

Posts images to the animeasthet07 Tumblr blog as photo posts automatically
after each successful Pinterest pin. Uses pytumblr (official library) with
direct binary upload — no reliance on Tumblr's URL fetcher.

Standalone test:
    python tumblr_uploader.py
"""

import os
import time
import requests
import pytumblr
from logger import get_logger

logger = get_logger(__name__)

_RETRY_DELAYS = [0, 3, 8]   # seconds before attempt 1, 2, 3


def _get_client():
    """Return an authenticated pytumblr client."""
    from config import (TUMBLR_CONSUMER_KEY, TUMBLR_CONSUMER_SECRET,
                        TUMBLR_ACCESS_TOKEN, TUMBLR_ACCESS_TOKEN_SECRET)
    if not TUMBLR_CONSUMER_KEY or not TUMBLR_ACCESS_TOKEN:
        raise ValueError("TUMBLR_CONSUMER_KEY and TUMBLR_ACCESS_TOKEN must be set in .env")
    return pytumblr.TumblrRestClient(
        TUMBLR_CONSUMER_KEY,
        TUMBLR_CONSUMER_SECRET,
        TUMBLR_ACCESS_TOKEN,
        TUMBLR_ACCESS_TOKEN_SECRET,
    )


def _download_to_temp(image_url):
    """Download an image URL to a local temp file. Returns path or None."""
    try:
        r = requests.get(image_url, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 Pinterest-Bot/1.0"})
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            import tempfile
            suffix = ".jpg"
            if "png" in r.headers.get("content-type", ""): suffix = ".png"
            if "webp" in r.headers.get("content-type", ""): suffix = ".webp"
            tmp = tempfile.mktemp(suffix=suffix)
            with open(tmp, "wb") as f:
                f.write(r.content)
            logger.debug("[Tumblr] Downloaded image to: " + tmp)
            return tmp
        else:
            logger.warning("[Tumblr] Could not download image: HTTP "
                           + str(r.status_code) + " content-type="
                           + r.headers.get("content-type", "?"))
            return None
    except Exception as e:
        logger.warning("[Tumblr] Download error: " + str(e))
        return None


def post_to_tumblr(image_url, title="", caption="", link="", tags=None,
                   image_path=None):
    """
    Post a photo to the configured Tumblr blog.

    Uses direct binary upload (pytumblr data= parameter) — much more reliable
    than Tumblr's URL fetcher which blocks many CDN domains.

    Args:
        image_url  : Public CDN URL (Cloudinary/Catbox) — used as fallback caption info
        title      : Post title (displayed bold in caption)
        caption    : Body text
        link       : Affiliate / product link
        tags       : List of tag strings (no # prefix)
        image_path : (optional) Local file path — preferred over URL download

    Returns:
        Post ID string on success, None on failure.
    """
    from config import TUMBLR_BLOG_NAME as BN, TUMBLR_ENABLED as EN, TUMBLR_TAGS as TG
    if not EN:
        logger.debug("[Tumblr] Disabled — skipping.")
        return None
    if not BN:
        logger.warning("[Tumblr] TUMBLR_BLOG_NAME not configured — skipping.")
        return None

    # Build HTML caption
    pts = []
    if title:   pts.append("<b>" + title + "</b>")
    if caption: pts.append(caption)
    if link:    pts.append("<a href=" + chr(34) + link + chr(34) + ">Shop on Amazon</a>")
    full_caption = "<br><br>".join(pts) if pts else title or ""

    # Merge default tags
    default_tags = [t.strip() for t in TG.split(",") if t.strip()]
    all_tags = list(dict.fromkeys((tags or []) + default_tags))

    # Resolve image: prefer local file > download URL > skip
    tmp_path = None
    img_data  = image_path   # local file from bot downloads/

    if not img_data and image_url:
        img_data = _download_to_temp(image_url)
        tmp_path = img_data  # remember to clean up

    if not img_data:
        logger.warning("[Tumblr] No image available to post — skipping.")
        return None

    try:
        client = _get_client()
    except ValueError as e:
        logger.error("[Tumblr] Config error: " + str(e))
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None

    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            result = client.create_photo(
                BN,
                caption = full_caption,
                tags    = all_tags,
                data    = img_data,    # direct binary upload — never fails with URL block
            )
            pid = result.get("id") or result.get("id_string")
            if pid:
                logger.info("[Tumblr] Posted id=" + str(pid)
                            + " title=" + title[:50]
                            + (" attempt=" + str(attempt) if attempt > 1 else ""))
                return str(pid)
            else:
                err = result.get("errors", result)
                logger.warning("[Tumblr] Attempt " + str(attempt)
                               + " failed: " + str(err))
        except Exception as e:
            logger.warning("[Tumblr] Exception on attempt " + str(attempt)
                           + ": " + str(e))

    logger.error("[Tumblr] All retries exhausted for: " + title)
    return None
    if tmp_path and os.path.exists(tmp_path):
        os.unlink(tmp_path)


def get_tumblr_blog_info():
    """Fetch blog metadata: title, post count, followers, URL. Returns dict or None."""
    from config import TUMBLR_BLOG_NAME as BN
    if not BN:
        return None
    try:
        client = _get_client()
        # Try blog_info endpoint first
        res  = client.blog_info(BN)
        blog = res.get("response", {}).get("blog", {})
        # Fallback: user/info has blog data for primary blog
        if not blog:
            uinfo = client.info()
            blogs = (uinfo.get("user", {}).get("blogs")
                     or uinfo.get("response", {}).get("user", {}).get("blogs")
                     or [])
            blog  = next((b for b in blogs if b.get("name") == BN), {})
        if blog:
            return {
                "title":     blog.get("title",       BN),
                "name":      blog.get("name",        BN),
                "url":       blog.get("url",         "https://" + BN + ".tumblr.com/"),
                "posts":     blog.get("total_posts", blog.get("posts", 0)),
                "followers": blog.get("followers",   0),
            }
        return None
    except Exception as e:
        logger.error("[Tumblr] Blog info error: " + str(e))
        return None


def verify_tumblr_token():
    """Check that the current access token is valid. Returns True/False."""
    try:
        client = _get_client()
        res    = client.info()
        # pytumblr returns nested: res['user']['name'] OR res['response']['user']['name']
        user = (res.get("user")
                or res.get("response", {}).get("user")
                or {})
        name = user.get("name", "")
        if name:
            logger.info("[Tumblr] Token valid — logged in as: " + name)
            return True
        # Also accepted: blogs array present
        if user.get("blogs"):
            logger.info("[Tumblr] Token valid — blogs found")
            return True
        logger.warning("[Tumblr] Token check returned no user name")
        return False
    except ValueError as e:
        logger.warning("[Tumblr] Config error: " + str(e))
        return False
    except Exception as e:
        logger.error("[Tumblr] Token check error: " + str(e))
        return False


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import glob

    print("=" * 60)
    print("  Tumblr Uploader — Standalone Test")
    print("=" * 60)

    print()
    print("[1] Verifying access token...")
    ok = verify_tumblr_token()
    print("    Valid:", ok)

    print()
    print("[2] Fetching blog info...")
    info = get_tumblr_blog_info()
    if info:
        print("    Blog     :", info["title"])
        print("    Posts    :", info["posts"])
        print("    Followers:", info["followers"])
        print("    URL      :", info["url"])
    else:
        print("    Unavailable (token not set?)")

    # Find a local image to use as test
    local_images = glob.glob("downloads/*.jpg") + glob.glob("downloads/*.png")
    test_img = local_images[0] if local_images else None

    print()
    print("[3] Posting test photo...")
    if test_img:
        print("    Using local image:", test_img)
        pid = post_to_tumblr(
            image_url  = "",
            title      = "Anime Aesthetic — Pinterest Bot Test",
            caption    = "Automated test post. This image is auto-curated from anime channels!",
            link       = "https://amazon.in",
            tags       = ["test", "anime", "aesthetic", "bot"],
            image_path = test_img,
        )
    else:
        print("    No local images in downloads/ — skipping photo test")
        pid = None

    print("    Result:", ("Success — ID=" + str(pid)) if pid else "Skipped or Failed")
    print()
    print("Done.")
