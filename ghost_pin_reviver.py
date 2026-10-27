"""
ghost_pin_reviver.py — Automatic Ghost Pin Re-Queue Engine
===========================================================
Finds pins uploaded 7+ days ago with zero bridge visits (no traffic),
re-queues them with a freshly generated alternative SEO title.

Trigger:
  - Automatically from scheduler (daily check)
  - On-demand via Telegram /revive command

New .env keys:
  GHOST_REVIVER_ENABLED=true
  GHOST_REVIVER_MIN_AGE_DAYS=7    # days since upload before declared ghost
  GHOST_REVIVER_MAX_BATCH=3       # max pins to revive per run
"""

import uuid
import random
import datetime
from logger import get_logger
import config

logger = get_logger(__name__)

REVIVER_ENABLED = getattr(config, "GHOST_REVIVER_ENABLED", True)
MIN_AGE_DAYS = int(getattr(config, "GHOST_REVIVER_MIN_AGE_DAYS", 7))
MAX_BATCH = int(getattr(config, "GHOST_REVIVER_MAX_BATCH", 3))


def _generate_revive_title(original_title: str, anime_name: str) -> str:
    """Creates a distinct alternative SEO title for the revived pin."""
    try:
        from seo_matrix import build_multi_intent_title

        angles = [
            f"{anime_name} Dark Aesthetic Wallpaper HD",
            f"{anime_name} Fan Art Poster Collection 4K",
            f"{anime_name} Manga Art Print Wall Decor",
            f"{anime_name} Minimalist Phone Wallpaper 4K",
        ]
        alt = random.choice(angles)
        return build_multi_intent_title(
            alt, anime_name=anime_name, character_name="", genre="wallpaper"
        )
    except Exception:
        return f"{anime_name} Anime Art 4K Wallpaper | {original_title[:35]}"


def run_ghost_reviver(dry_run: bool = False) -> list[dict]:
    """
    Main entry point. Scans for ghost pins and re-queues them.
    Returns list of revived pin dicts.
    """
    if not REVIVER_ENABLED and not dry_run:
        return []

    from database import (
        get_uploaded_files_with_zero_visits,
        enqueue_pin,
        record_tracked_link,
        _get_conn,
    )

    ghosts = get_uploaded_files_with_zero_visits(days_old=MIN_AGE_DAYS, limit=MAX_BATCH)
    if not ghosts:
        logger.info("[GhostReviver] No ghost pins found.")
        return []

    revived = []
    today_str = (
        datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    ).strftime("%Y-%m-%d")
    base_url = (getattr(config, "APP_BASE_URL", "") or "").rstrip("/")

    for ghost in ghosts:
        try:
            anime = ghost.get("anime") or "Anime"
            orig = ghost.get("title") or "Anime Art"
            img_url = ghost.get("image_url") or ""

            if not img_url:
                logger.debug(f"[GhostReviver] Skipping '{orig}' — no image_url stored.")
                continue

            # Skip if already revived recently (within last 14 days)
            with _get_conn() as conn:
                already = conn.execute("""
                    SELECT 1 FROM ghost_revive_log
                    WHERE original_title = ? AND anime_name = ?
                      AND date(revived_at) >= date('now', '-14 days')
                """, (orig, anime)).fetchone()
            if already:
                logger.debug(f"[GhostReviver] '{orig}' was revived recently, skipping.")
                continue

            new_title = _generate_revive_title(orig, anime)
            code = f"rv_{uuid.uuid4().hex[:6]}"
            try:
                from amazon_search import generate_amazon_link
                amazon_url = generate_amazon_link(anime, title=new_title)
            except Exception:
                amazon_url = f"https://www.amazon.com/s?k={anime.replace(' ', '+')}+anime+merch"
            bridge_url = f"{base_url}/p/{code}" if base_url else amazon_url
            post_id = f"revive_{code}"

            # Build fresh multi-intent description
            try:
                from seo_matrix import (
                    build_multi_intent_description,
                    generate_multi_intent_tags,
                )

                desc = build_multi_intent_description(
                    base_description=f"Revived: {anime} anime aesthetic art 4K wallpaper.",
                    anime_name=anime,
                    character_name="",
                    link_url=bridge_url,
                )
                tags = generate_multi_intent_tags(
                    anime_name=anime, character_name="", genre="", target_count=10
                )
                full_desc = f"{desc}\n\n{tags}\n#ad #affiliate"
            except Exception:
                full_desc = f"{new_title}\n\nShop anime merch: {bridge_url}\n#ad #affiliate"

            if not dry_run:
                record_tracked_link(
                    code=code,
                    target_url=amazon_url,
                    anime_name=anime,
                    title=new_title,
                    image_url=img_url,
                )
                enqueued = enqueue_pin(
                    post_id=post_id,
                    image_path="",
                    title=new_title,
                    description=full_desc,
                    link=bridge_url,
                    anime_name=anime,
                    image_url=img_url,
                    priority=0,
                    scheduled_date=today_str,
                    allow_duplicate=True,
                )
                if not enqueued:
                    logger.warning(f"[GhostReviver] Could not enqueue revived pin for '{anime}' ({post_id})")
                    continue
                # Log to ghost_revive_log
                with _get_conn() as conn:
                    conn.execute("""
                        INSERT INTO ghost_revive_log
                            (original_post_id, new_post_id, anime_name,
                             original_title, new_title, image_url)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (ghost["filename"], post_id, anime, orig, new_title, img_url))
                    conn.commit()

            logger.info(f"[GhostReviver] Revived '{anime}': new title -> '{new_title[:60]}'")
            revived.append({
                "anime": anime,
                "original": orig,
                "new_title": new_title,
                "dry_run": dry_run,
            })

        except Exception as e:
            logger.error(f"[GhostReviver] Failed to revive '{ghost.get('title')}': {e}")

    return revived


def format_revive_report(revived: list[dict]) -> str:
    """Formats result of run_ghost_reviver() for Telegram."""
    if not revived:
        return (
            "👻 *Ghost Pin Reviver*\n\n"
            f"No ghost pins found (≥{MIN_AGE_DAYS} days old with 0 visits).\n"
            "_All active pins are getting traffic or no unrevived pins found._ ✅"
        )

    lines = [f"👻 *Ghost Pin Reviver* — {len(revived)} pin(s) revived!\n"]
    for i, r in enumerate(revived, 1):
        dr = " _(dry run)_" if r["dry_run"] else ""
        lines.append(
            f"*{i}. {r['anime']}*{dr}\n"
            f"  Old: _{r['original'][:55]}_\n"
            f"  New: _{r['new_title'][:55]}_\n"
        )
    lines.append("_Revived pins added to backlog queue._")
    return "\n".join(lines)


def check_and_run_daily_reviver() -> int:
    """Called from scheduler loop. Runs once per day via bot_metadata guard."""
    if not getattr(config, "GHOST_REVIVER_ENABLED", True):
        return 0
    try:
        from database import get_metadata, set_metadata

        today = (
            datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        ).strftime("%Y-%m-%d")
        if get_metadata("last_ghost_revive_date", "") == today:
            return 0
        revived = run_ghost_reviver()
        set_metadata("last_ghost_revive_date", today)
        return len(revived)
    except Exception as e:
        logger.error(f"[GhostReviver] Daily check failed: {e}")
        return 0
