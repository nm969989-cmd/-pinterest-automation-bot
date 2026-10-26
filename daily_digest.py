"""
daily_digest.py — Automated Daily Performance Digest for Telegram
==================================================================
Compiles and delivers a daily performance summary directly to the Telegram
admin chat once per day (default 21:00 IST / customizable) and on-demand via
the /digest command.

Monitors:
  - Pinterest & Queue: Posts today vs target, queue buffer days, failed pins.
  - Funnel & Clicks: Bridge page visits, Pinterest referrals, Amazon clicks, CTR.
  - Top Performer: Best converting anime of the day.
  - Render Free Tier Health: RAM usage (MB / 512 MB), disk space, circuit breakers.

Deduplication:
  Uses SQLite bot_metadata ('last_daily_digest_date') so restarts/deploys on Render
  never send duplicate messages on the same day.
"""

import datetime
from logger import get_logger

logger = get_logger(__name__)

# Default 7 platforms to inspect for circuit breaker status
_PLATFORMS = ["bluesky", "mastodon", "pixelfed", "arena", "raindrop", "freeimage", "imghippo"]


def _get_ist_now() -> datetime.datetime:
    """Returns current datetime in Indian Standard Time (UTC + 5:30)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def build_daily_digest_message(date_str: str = None) -> str:
    """
    Assembles a comprehensive, clean Markdown performance report from
    local SQLite, analytics, and system health checks.
    """
    now_ist = _get_ist_now()
    today_str = date_str or now_ist.strftime("%Y-%m-%d")
    display_date = now_ist.strftime("%d %b %Y")

    # 1. Posting & Queue stats
    try:
        import config
        from database import count_posts_today, get_queue_counts, count_dead_letter_pins

        posts_today = count_posts_today(today_str)
        max_posts = getattr(config, "MAX_POSTS_PER_DAY", 5)
        queue_counts = get_queue_counts()
        q_total = queue_counts.get("total", 0)
        q_new = queue_counts.get("new", 0)
        dead_letter = count_dead_letter_pins()

        # Content buffer in days
        buffer_days = round(q_total / max(1, max_posts), 1) if q_total > 0 else 0.0
    except Exception as e:
        logger.warning(f"[DailyDigest] Failed gathering queue stats: {e}")
        posts_today, max_posts, q_total, q_new, dead_letter, buffer_days = 0, 5, 0, 0, 0, 0.0

    # 2. Funnel & Analytics
    try:
        from pinterest_analytics import get_analytics_summary
        analytics = get_analytics_summary(days=7)
        today_visits = analytics.get("today_visits", 0)
        today_clicks = analytics.get("today_clicks", 0)
        today_ctr = round(today_clicks / max(1, today_visits) * 100, 1) if today_visits > 0 else 0.0

        bridge_total_7d = analytics.get("bridge_visits", 0)
        clicks_7d = analytics.get("amazon_clicks", 0)
        ctr_7d = analytics.get("ctr", 0.0)
        pint_visits = analytics.get("pinterest_visits", 0)
        pint_rate = analytics.get("pint_rate", 0.0)
        top_anime_list = analytics.get("top_anime", [])
    except Exception as e:
        logger.warning(f"[DailyDigest] Failed gathering analytics: {e}")
        today_visits, today_clicks, today_ctr = 0, 0, 0.0
        bridge_total_7d, clicks_7d, ctr_7d, pint_visits, pint_rate = 0, 0, 0.0, 0, 0.0
        top_anime_list = []

    # 3. Top Performer
    top_performer_text = "None recorded yet today"
    if top_anime_list:
        name, visits, p_vis = top_anime_list[0]
        top_performer_text = f"*{name}* ({visits} visits"
        if p_vis:
            top_performer_text += f", {p_vis} from Pinterest"
        top_performer_text += ")"
    today_merch_breakdown = ""
    try:
        from database import get_click_stats
        c_stats = get_click_stats()
        tbm = c_stats.get("today_by_merchant", {})
        if tbm and sum(tbm.values()) > 0:
            parts = []
            if tbm.get("amazon"): parts.append(f"🛒 {tbm['amazon']} Amazon")
            if tbm.get("displate"): parts.append(f"🖼️ {tbm['displate']} Displate")
            if tbm.get("wallpaper"): parts.append(f"📱 {tbm['wallpaper']} Wallpaper")
            if parts:
                today_merch_breakdown = f" ({' | '.join(parts)})"

        if not top_anime_list and c_stats.get("top_anime"):
            c_name, c_cnt = c_stats["top_anime"][0]
            top_performer_text = f"*{c_name}* ({c_cnt} affiliate click{'s' if c_cnt != 1 else ''})"
    except Exception:
        pass

    # 4. Render Free-Tier Health
    try:
        from crash_protection import get_memory_mb, get_disk_usage_mb
        mem_mb = round(get_memory_mb(), 1)
        disk_mb = round(get_disk_usage_mb("downloads") + get_disk_usage_mb("processed"), 1)
        mem_pct = round((mem_mb / 512.0) * 100, 1) if mem_mb > 0 else 0.0
        mem_status = "🟢" if mem_mb < 350 else ("🟡" if mem_mb < 450 else "🔴 HIGH")
    except Exception as e:
        logger.warning(f"[DailyDigest] Failed gathering system stats: {e}")
        mem_mb, mem_pct, disk_mb, mem_status = 0.0, 0.0, 0.0, "⚪"

    # 5. Circuit Breaker status
    active_platforms = 0
    cooling_platforms = []
    try:
        from circuit_breaker import is_cooling_down
        for plat in _PLATFORMS:
            cooling, reason, rem = is_cooling_down(plat)
            if cooling:
                cooling_platforms.append(f"{plat} ({rem}h left)")
            else:
                active_platforms += 1
    except Exception as e:
        logger.warning(f"[DailyDigest] Failed checking circuit breakers: {e}")
        active_platforms = len(_PLATFORMS)

    breaker_text = f"{active_platforms}/{len(_PLATFORMS)} online"
    if cooling_platforms:
        breaker_text += f" ⚠️ ({', '.join(cooling_platforms)})"

    # Assemble report
    post_icon = "✅" if posts_today >= max_posts else "⏳"
    lines = [
        f"📊 *Daily Performance Digest* — {display_date}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📌 *Pinterest & Scheduling*",
        f"  • Posted Today: *{posts_today}* / {max_posts} pins {post_icon}",
        f"  • Remaining Queue: *{q_total}* pins (~{buffer_days} days buffer)",
        f"  • Priority Queue: *{q_new}* fresh pin{'s' if q_new != 1 else ''}",
    ]

    if dead_letter > 0:
        lines.append(f"  • ⚠️ Dead-Letter (Failed): *{dead_letter}* pins")

    lines.extend([
        "",
        "🌐 *Traffic & Affiliate Funnel*",
        f"  • Bridge Page Visits: *{today_visits}* today",
        f"  • Referrer from Pinterest: *{pint_rate}%*",
        f"  • Affiliate Clicks: *{today_clicks}* today{today_merch_breakdown}",
        f"  • Daily CTR: *{today_ctr}%* 🎯",
        f"  • 7-Day Funnel: {bridge_total_7d} visits → {clicks_7d} clicks ({ctr_7d}% CTR)",
    ])

    try:
        from database import get_backlink_stats
        b_stats = get_backlink_stats()
        if b_stats.get("total", 0) > 0 or b_stats.get("today", 0) > 0:
            lines.append(f"  • SEO Backlinks: *{b_stats.get('today', 0)}* today ({b_stats['total']} all-time across 7 platforms)")
    except Exception:
        pass

    lines.extend([
        "",
        "🏆 *Top Content*",
        f"  • {top_performer_text}",
        "",
        "🛡️ *Render Free-Tier Health*",
        f"  • RAM: *{mem_mb} MB* / 512 MB ({mem_pct}%) {mem_status}",
        f"  • Ephemeral Disk: *{disk_mb} MB*",
        f"  • Platform Breakers: *{breaker_text}*",
        "",
        "💡 _Use /queue to inspect pending pins or /stats for totals._",
    ])

    return "\n".join(lines)


def send_daily_digest(force: bool = False) -> bool:
    """
    Sends the formatted daily performance digest to the admin chat.
    If force=False, checks whether today's digest was already sent.
    Returns True if sent, False if skipped.
    """
    now_ist = _get_ist_now()
    today_str = now_ist.strftime("%Y-%m-%d")

    from database import get_metadata, set_metadata

    last_sent = get_metadata("last_daily_digest_date", "").strip()
    if not force and last_sent == today_str:
        logger.info(f"[DailyDigest] Digest for {today_str} was already sent. Skipping.")
        return False

    try:
        report = build_daily_digest_message(date_str=today_str)
        from telegram_bot import notify_admin
        notify_admin(report, parse_mode="Markdown")
        set_metadata("last_daily_digest_date", today_str)
        logger.info(f"[DailyDigest] Successfully delivered daily performance digest for {today_str}.")
        return True
    except Exception as e:
        logger.error(f"[DailyDigest] Failed to send daily digest: {e}", exc_info=True)
        return False


def check_and_send_scheduled_digest() -> bool:
    """
    Called from the scheduler loop. Checks if the scheduled digest time
    has arrived for today (IST) and sends if not already sent.
    """
    try:
        import config
        if not getattr(config, "DAILY_DIGEST_ENABLED", True):
            return False

        target_time = getattr(config, "DAILY_DIGEST_TIME", "21:00").strip()
        now_ist = _get_ist_now()
        current_time = now_ist.strftime("%H:%M")

        # Has the scheduled time arrived?
        if current_time >= target_time:
            today_str = now_ist.strftime("%Y-%m-%d")
            from database import get_metadata
            last_sent = get_metadata("last_daily_digest_date", "").strip()

            if last_sent != today_str:
                logger.info(
                    f"[DailyDigest] Triggering scheduled digest (current: {current_time} IST, target: {target_time} IST)"
                )
                return send_daily_digest(force=False)

    except Exception as e:
        logger.warning(f"[DailyDigest] Scheduled check error: {e}")

    return False
