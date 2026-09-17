"""
human_simulator.py — Organic Human Activity & Anti-Bot Protection Engine
========================================================================
Protects social platform accounts (Bluesky, Mastodon, Pixelfed) from
automated bot classification and suspensions.

Social network spam algorithms monitor account behavior ratios:
  - Broadcast ratio (pure POST uploads vs reading feeds)
  - Community engagement (likes, favorites, browsing)
  - Posting regularity (clockwork timing vs human jitter)

This module simulates realistic human browsing:
  1. Timeline & Discovery Reading (GET requests to fetch recent art feeds)
  2. Occasional, strictly-capped Community Liking (max 1-2 likes/day)
  3. Safe tracking in SQLite bot_metadata to prevent spam or over-engagement
"""

import time
import random
import datetime
import requests
from logger import get_logger

logger = get_logger(__name__)

_MAX_DAILY_LIKES = 2


def _get_today_str() -> str:
    """Returns today's date in IST."""
    ist_now = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    return ist_now.strftime("%Y-%m-%d")


def _get_daily_action_count(platform: str, action: str) -> int:
    """Retrieves count of simulated actions today from SQLite bot_metadata."""
    try:
        from database import get_metadata
        key = f"sim_{platform}_{action}_{_get_today_str()}"
        val = get_metadata(key, "0")
        return int(val)
    except Exception:
        return 0


def _increment_daily_action_count(platform: str, action: str):
    """Increments count of simulated actions today in SQLite bot_metadata."""
    try:
        from database import set_metadata
        current = _get_daily_action_count(platform, action)
        key = f"sim_{platform}_{action}_{_get_today_str()}"
        set_metadata(key, str(current + 1))
    except Exception as e:
        logger.debug(f"[HumanSim] Could not record action count: {e}")


# ── 1. Bluesky Human Activity ─────────────────────────────────────────────────

def simulate_bluesky_activity() -> bool:
    """
    Simulates organic Bluesky browsing:
      - Reads recent posts tagged #anime from the global firehose/search.
      - If under the daily cap, likes 1 high-quality artwork post.
    """
    import config
    if not getattr(config, "BLUESKY_ENABLED", False):
        return False

    try:
        from bluesky_uploader import _get_session, BSKY_XRPC_BASE
        jwt, did, handle = _get_session()
        if not jwt or not did:
            return False

        headers = {"Authorization": f"Bearer {jwt}"}

        # 1. Human read action: Search recent #anime art posts
        search_url = f"{BSKY_XRPC_BASE}/app.bsky.feed.searchPosts"
        query_tags = ["#animeart", "#anime", "#illustration", "#manga"]
        chosen_tag = random.choice(query_tags)

        r = requests.get(search_url, headers=headers, params={"q": chosen_tag, "limit": 10}, timeout=15)
        if r.status_code != 200:
            logger.debug(f"[HumanSim:Bluesky] Feed browse returned HTTP {r.status_code}")
            return False

        posts = r.json().get("posts", [])
        if not posts:
            return False

        logger.info(f"[HumanSim:Bluesky] Browsed feed ({len(posts)} posts found for '{chosen_tag}')")

        # 2. Check daily like quota
        likes_today = _get_daily_action_count("bluesky", "like")
        if likes_today >= _MAX_DAILY_LIKES:
            logger.debug(f"[HumanSim:Bluesky] Daily like limit reached ({likes_today}/{_MAX_DAILY_LIKES}). Read-only mode.")
            return True

        # 3. Pick a suitable post to like
        candidate_posts = [p for p in posts if p.get("author", {}).get("did") != did]
        if not candidate_posts:
            return True

        target_post = random.choice(candidate_posts[:5])
        post_uri = target_post.get("uri")
        post_cid = target_post.get("cid")
        author_handle = target_post.get("author", {}).get("handle", "user")

        if not post_uri or not post_cid:
            return True

        # Simulate natural human read pause before liking (3 to 8 seconds)
        time.sleep(random.uniform(3, 8))

        like_record = {
            "$type": "app.bsky.feed.like",
            "subject": {
                "uri": post_uri,
                "cid": post_cid,
            },
            "createdAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        like_url = f"{BSKY_XRPC_BASE}/com.atproto.repo.createRecord"
        payload = {
            "repo": did,
            "collection": "app.bsky.feed.like",
            "record": like_record,
        }

        res = requests.post(like_url, headers=headers, json=payload, timeout=15)
        if res.status_code == 200:
            _increment_daily_action_count("bluesky", "like")
            logger.info(f"[HumanSim:Bluesky] Liked post by @{author_handle} (quota: {likes_today + 1}/{_MAX_DAILY_LIKES})")
            return True
        else:
            logger.debug(f"[HumanSim:Bluesky] Like request returned HTTP {res.status_code}")

    except Exception as e:
        logger.debug(f"[HumanSim:Bluesky] Simulation exception: {e}")

    return False


# ── 2. Mastodon Human Activity ────────────────────────────────────────────────

def simulate_mastodon_activity() -> bool:
    """
    Simulates organic Mastodon browsing:
      - Reads public #anime tag timeline.
      - If under the daily cap, favorites 1 anime/art toot.
    """
    import config
    if not getattr(config, "MASTODON_ENABLED", False):
        return False

    try:
        from mastodon_uploader import _get_instance_url, _get_headers
        base_url = _get_instance_url()
        headers = _get_headers()

        # 1. Human read action: Browse tag timeline
        tag = random.choice(["anime", "manga", "animeart", "illustration"])
        url = f"{base_url}/api/v1/timelines/tag/{tag}"
        r = requests.get(url, headers=headers, params={"limit": 10}, timeout=15)
        if r.status_code != 200:
            return False

        statuses = r.json()
        if not isinstance(statuses, list) or not statuses:
            return False

        logger.info(f"[HumanSim:Mastodon] Browsed timeline ({len(statuses)} toots found for '#{tag}')")

        # 2. Check daily like quota
        likes_today = _get_daily_action_count("mastodon", "favourite")
        if likes_today >= _MAX_DAILY_LIKES:
            logger.debug(f"[HumanSim:Mastodon] Daily favourite limit reached ({likes_today}/{_MAX_DAILY_LIKES}).")
            return True

        target_status = random.choice(statuses[:5])
        status_id = target_status.get("id")
        author = target_status.get("account", {}).get("username", "user")

        if not status_id:
            return True

        time.sleep(random.uniform(3, 7))

        fav_url = f"{base_url}/api/v1/statuses/{status_id}/favourite"
        res = requests.post(fav_url, headers=headers, timeout=15)
        if res.status_code == 200:
            _increment_daily_action_count("mastodon", "favourite")
            logger.info(f"[HumanSim:Mastodon] Favourited toot by @{author} (quota: {likes_today + 1}/{_MAX_DAILY_LIKES})")
            return True

    except Exception as e:
        logger.debug(f"[HumanSim:Mastodon] Simulation exception: {e}")

    return False


# ── 3. Pixelfed Human Activity ────────────────────────────────────────────────

def simulate_pixelfed_activity() -> bool:
    """
    Simulates organic Pixelfed browsing:
      - Reads public photo feed.
      - If under the daily cap, likes 1 photo post.
    """
    import config
    if not getattr(config, "PIXELFED_ENABLED", False):
        return False

    try:
        from pixelfed_uploader import _get_instance_url, _get_headers
        base_url = _get_instance_url()
        headers = _get_headers()

        # 1. Human read action: Browse public timeline
        url = f"{base_url}/api/v1/timelines/public"
        r = requests.get(url, headers=headers, params={"limit": 10}, timeout=15)
        if r.status_code != 200:
            return False

        posts = r.json()
        if not isinstance(posts, list) or not posts:
            return False

        logger.info(f"[HumanSim:Pixelfed] Browsed public feed ({len(posts)} photo posts found)")

        # 2. Check daily like quota
        likes_today = _get_daily_action_count("pixelfed", "favourite")
        if likes_today >= _MAX_DAILY_LIKES:
            logger.debug(f"[HumanSim:Pixelfed] Daily like limit reached ({likes_today}/{_MAX_DAILY_LIKES}).")
            return True

        target_post = random.choice(posts[:5])
        post_id = target_post.get("id")
        author = target_post.get("account", {}).get("username", "user")

        if not post_id:
            return True

        time.sleep(random.uniform(3, 7))

        fav_url = f"{base_url}/api/v1/statuses/{post_id}/favourite"
        res = requests.post(fav_url, headers=headers, timeout=15)
        if res.status_code == 200:
            _increment_daily_action_count("pixelfed", "favourite")
            logger.info(f"[HumanSim:Pixelfed] Liked photo post by @{author} (quota: {likes_today + 1}/{_MAX_DAILY_LIKES})")
            return True

    except Exception as e:
        logger.debug(f"[HumanSim:Pixelfed] Simulation exception: {e}")

    return False


# ── Master Simulation Runner ──────────────────────────────────────────────────

def run_organic_simulation() -> dict:
    """
    Executes a round of organic browsing & simulation across active platforms.
    Safe to call before scheduled posting or periodically.
    """
    logger.info("[HumanSim] Initiating organic human activity cycle...")
    results = {
        "bluesky": False,
        "mastodon": False,
        "pixelfed": False,
    }

    try:
        results["bluesky"] = simulate_bluesky_activity()
    except Exception as e:
        logger.debug(f"[HumanSim] Bluesky error: {e}")

    time.sleep(random.uniform(2, 5))

    try:
        results["mastodon"] = simulate_mastodon_activity()
    except Exception as e:
        logger.debug(f"[HumanSim] Mastodon error: {e}")

    time.sleep(random.uniform(2, 5))

    try:
        results["pixelfed"] = simulate_pixelfed_activity()
    except Exception as e:
        logger.debug(f"[HumanSim] Pixelfed error: {e}")

    logger.info(f"[HumanSim] Organic human activity cycle completed: {results}")
    return results


if __name__ == "__main__":
    print("Testing Organic Human Activity Simulator...")
    res = run_organic_simulation()
    print(f"Results: {res}")
