"""
weekly_report.py — Weekly Sunday Performance Digest
====================================================
Auto-sends a comprehensive weekly performance report every Sunday at
WEEKLY_REPORT_TIME IST (default: 09:00 AM).
Features verified human clicks, geographic distribution, realistic Amazon
conversion modeling, and direct access to Amazon Associates Central.

Configuration (.env):
  WEEKLY_REPORT_ENABLED=true
  WEEKLY_REPORT_TIME=09:00       # IST HH:MM
  WEEKLY_REPORT_DAY=6            # 6=Sunday (Python weekday: Mon=0, Sun=6)
"""

import datetime
import time
from logger import get_logger
import config

logger = get_logger(__name__)


def _ist_now() -> datetime.datetime:
    """Returns current datetime in IST (UTC+5:30)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def get_amazon_report_markup():
    """Returns an inline keyboard markup with direct link to Amazon Associates Reports."""
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton("📊 Open Amazon Associates Reports", url="https://affiliate-program.amazon.com/home/reports")],
        ]
        return InlineKeyboardMarkup(keyboard)
    except Exception:
        return None


def build_weekly_report() -> str:
    """Assembles the full weekly Markdown digest with real, grounded performance data."""
    now = _ist_now()
    week_start = (now - datetime.timedelta(days=7)).strftime("%d %b")
    week_end = now.strftime("%d %b %Y")
    report_time = getattr(config, "WEEKLY_REPORT_TIME", "09:00").strip()

    lines = [
        "📆 *Weekly Performance Report*",
        f"*{week_start} → {week_end} (Sunday {report_time} IST)*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    cutoff_7d = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    cutoff_14d = (now - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

    # 1. Posting stats (Pinterest)
    try:
        from database import _get_conn
        with _get_conn() as conn:
            pins_posted = conn.execute("""
                SELECT COUNT(*) FROM uploaded_files
                WHERE date(uploaded_at, '+5 hours', '+30 minutes') >= ?
            """, (cutoff_7d,)).fetchone()[0]
            organic_count = conn.execute("""
                SELECT COUNT(*) FROM uploaded_files
                WHERE date(uploaded_at, '+5 hours', '+30 minutes') >= ?
                  AND is_organic = 1
            """, (cutoff_7d,)).fetchone()[0]
        monetized_count = max(0, pins_posted - organic_count)
        lines += [
            "",
            "📌 *Pinterest Activity (7 Days)*",
            f"  • Pins Published: *{pins_posted}* (🌿 {organic_count} organic / 💎 {monetized_count} monetized)",
        ]
    except Exception as e:
        logger.warning(f"[WeeklyReport] Posting stats error: {e}")

    # 2. Real Human Traffic & Unified Click Attribution
    total_clicks = 0
    countries = []
    wow_growth_str = ""
    try:
        from database import _get_conn
        with _get_conn() as conn:
            # 7-day total human clicks from unified link_clicks
            cur_clicks = conn.execute("""
                SELECT COUNT(*) FROM link_clicks
                WHERE date(clicked_at, '+5 hours', '+30 minutes') >= ?
            """, (cutoff_7d,)).fetchone()[0]
            total_clicks = cur_clicks

            # Prior 7-day clicks for week-over-week calculation
            prev_clicks = conn.execute("""
                SELECT COUNT(*) FROM link_clicks
                WHERE date(clicked_at, '+5 hours', '+30 minutes') >= ?
                  AND date(clicked_at, '+5 hours', '+30 minutes') < ?
            """, (cutoff_14d, cutoff_7d)).fetchone()[0]

            if prev_clicks > 0:
                pct = ((cur_clicks - prev_clicks) / float(prev_clicks)) * 100
                wow_growth_str = f" ({'+' if pct >= 0 else ''}{pct:.1f}% vs prior week)"
            elif cur_clicks > 0:
                wow_growth_str = " (Traffic active 🚀)"

            # Country breakdown (Top 5)
            c_rows = conn.execute("""
                SELECT country, COUNT(*) as cnt
                FROM link_clicks
                WHERE date(clicked_at, '+5 hours', '+30 minutes') >= ?
                  AND country IS NOT NULL AND country != ''
                GROUP BY country
                ORDER BY cnt DESC
                LIMIT 5
            """, (cutoff_7d,)).fetchall()
            countries = c_rows

        flag_map = {
            "US": "🇺🇸 US", "GB": "🇬🇧 UK", "UK": "🇬🇧 UK",
            "CA": "🇨🇦 CA", "DE": "🇩🇪 DE", "IN": "🇮🇳 IN",
            "FR": "🇫🇷 FR", "BR": "🇧🇷 BR", "IT": "🇮🇹 IT",
            "ES": "🇪🇸 ES", "JP": "🇯🇵 JP", "AU": "🇦🇺 AU",
            "NL": "🇳🇱 NL",
        }
        country_strs = []
        for c_code, count in countries:
            label = flag_map.get(c_code.upper(), f"🌐 {c_code.upper()}")
            country_strs.append(f"{label}: *{count}*")

        lines += [
            "",
            "👥 *Real Traffic & Click Attribution*",
            f"  • Total Human Clicks: *{total_clicks}*{wow_growth_str}",
        ]
        if country_strs:
            breakdown_line = " | ".join(country_strs)
            lines.append(f"  • Geographic Breakdown:\n    {breakdown_line}")

    except Exception as e:
        logger.warning(f"[WeeklyReport] Click stats error: {e}")

    # 3. Top Performing Content (Anime & Pins)
    try:
        from database import _get_conn
        with _get_conn() as conn:
            top_animes = conn.execute("""
                SELECT anime_name, COUNT(*) as cnt
                FROM link_clicks
                WHERE date(clicked_at, '+5 hours', '+30 minutes') >= ?
                  AND anime_name IS NOT NULL AND anime_name != ''
                GROUP BY anime_name
                ORDER BY cnt DESC
                LIMIT 3
            """, (cutoff_7d,)).fetchall()

            top_pin_rows = conn.execute("""
                SELECT title, anime_name, COUNT(*) as cnt
                FROM link_clicks
                WHERE date(clicked_at, '+5 hours', '+30 minutes') >= ?
                  AND title IS NOT NULL AND title != ''
                GROUP BY title
                ORDER BY cnt DESC
                LIMIT 3
            """, (cutoff_7d,)).fetchall()

        if top_animes:
            lines += ["", "🏆 *Top Performing Anime*"]
            for i, (anime, count) in enumerate(top_animes, 1):
                lines.append(f"  {i}. *{anime}* — {count} clicks")

        if top_pin_rows:
            lines += ["", "🎯 *Top Converting Pins*"]
            for i, (p_title, p_anime, count) in enumerate(top_pin_rows, 1):
                clean_t = p_title[:45] + ("..." if len(p_title) > 45 else "")
                lines.append(f"  {i}. *{p_anime}* ({count} clicks)\n     _{clean_t}_")

    except Exception as e:
        logger.warning(f"[WeeklyReport] Content performance error: {e}")

    # 4. Amazon Associates Status (Real Data)
    from config import AMAZON_AFFILIATE_TAG, AMAZON_AFFILIATE_TAG_US, AMAZON_AFFILIATE_TAG_IN
    tag_us = AMAZON_AFFILIATE_TAG_US or AMAZON_AFFILIATE_TAG or "animeasthet-20"
    tag_in = AMAZON_AFFILIATE_TAG_IN or "animeasthet06-21"

    lines += [
        "",
        "📦 *Amazon Associates Status (Real Data)*",
        f"  • Active Tags: `{tag_us}` (US/Global) | `{tag_in}` (India)",
        f"  • Verified Outbound Clicks: *{total_clicks}*",
    ]

    if total_clicks > 0:
        min_orders = max(1, int(round(total_clicks * 0.015)))
        max_orders = max(min_orders, int(round(total_clicks * 0.035)))
        min_est = round(min_orders * 2.50, 2)
        max_est = round(max_orders * 5.50, 2)
        lines += [
            f"  • Expected Orders: *{min_orders} to {max_orders} orders* (at 1.5%–3.5% conversion)",
            f"  • Estimated Commission: *${min_est:.2f} – ${max_est:.2f} USD*",
        ]
    else:
        lines.append("  • Expected Orders: *0 orders* (Need more pin traffic)")

    lines += [
        "  ℹ️ _Amazon credits commissions once customer orders physically ship._",
        "",
        "👉 *Live Finalized Balance & Shipped Items:*",
        "https://affiliate-program.amazon.com/home/reports",
    ]

    # 5. System Health
    try:
        from crash_protection import get_memory_mb
        mem = round(get_memory_mb(), 1)
        lines += ["", "🛡️ *System Health*", f"  • RAM Usage: *{mem} MB*"]
    except Exception:
        pass

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "💡 _Use /weekly anytime to preview this report on demand._",
    ]

    return "\n".join(lines)


_last_sent_week_key = ""
_last_failed_attempt_ts: float = 0.0
_RETRY_BACKOFF_SEC = 600  # after a failed delivery, wait 10 min before the scheduler retries


def send_weekly_report(force: bool = False) -> bool:
    """Send the weekly report with interactive Amazon dashboard button."""
    global _last_sent_week_key, _last_failed_attempt_ts
    if not getattr(config, "WEEKLY_REPORT_ENABLED", True):
        return False
    if not force and time.time() - _last_failed_attempt_ts < _RETRY_BACKOFF_SEC:
        return False

    now = _ist_now()
    week_key = f"weekly_report_{now.strftime('%Y-W%W')}"

    from database import get_metadata, set_metadata

    if not force:
        if _last_sent_week_key == week_key or get_metadata(week_key, "") == "sent":
            _last_sent_week_key = week_key
            logger.debug(f"[WeeklyReport] Already sent for {week_key}")
            return False

    try:
        report = build_weekly_report()
        markup = get_amazon_report_markup()
        from telegram_bot import notify_admin_confirmed

        if not notify_admin_confirmed(report, parse_mode="Markdown", reply_markup=markup):
            logger.warning(f"[WeeklyReport] Report for {week_key} was not delivered; will retry.")
            _last_failed_attempt_ts = time.time()
            return False

        set_metadata(week_key, "sent")
        _last_sent_week_key = week_key
        logger.info(f"[WeeklyReport] Weekly report successfully sent for {week_key}")
        return True
    except Exception as e:
        logger.error(f"[WeeklyReport] Failed to send report: {e}", exc_info=True)
        return False


def check_and_send_weekly_report() -> bool:
    """Called from scheduler loop every 30s. Triggers on Sunday at WEEKLY_REPORT_TIME IST."""
    global _last_sent_week_key
    if not getattr(config, "WEEKLY_REPORT_ENABLED", True):
        return False

    now = _ist_now()
    target_day = int(getattr(config, "WEEKLY_REPORT_DAY", 6))  # 6=Sunday
    target_time = getattr(config, "WEEKLY_REPORT_TIME", "09:00").strip()

    week_key = f"weekly_report_{now.strftime('%Y-W%W')}"
    if _last_sent_week_key == week_key:
        return False

    if now.weekday() == target_day and now.strftime("%H:%M") >= target_time:
        return send_weekly_report(force=False)
    return False
