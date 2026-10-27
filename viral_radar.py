"""
viral_radar.py — Viral Pin Surge Radar & Auto-Spinoff Engine
============================================================
Detects real-time traffic surges on Pinterest bridge pages (/p/<code>),
alerts the admin via Telegram with one-tap action buttons, and generates or
boosts Express-Priority (priority=2) spinoff pins to ride active algorithm waves.

Key Features:
  - Rolling window velocity & surge score calculation (Pinterest search weighted x2)
  - Automatic alert suppression cooldown to prevent notification fatigue
  - Telegram interactive buttons: [🔥 Spinoff 2 Pins], [⚡ Express Priority #1], [📊 Pin Analytics]
  - Priority 2 Express Queue promotion: Jumps to the very front of the queue
  - Dynamic multi-intent angle variations (AMOLED Wallpaper vs Displate Room Decor)
"""

import datetime
import random
import uuid
import time
import threading
import json
from logger import get_logger
from config import (
    VIRAL_RADAR_ENABLED,
    VIRAL_SURGE_MIN_VISITS,
    VIRAL_ALERT_COOLDOWN_HOURS,
    VIRAL_AUTO_SPINOFF,
    VIRAL_MAX_DAILY_ALERTS,
    APP_BASE_URL,
    AMAZON_AFFILIATE_TAG,
)
from database import (
    _get_conn,
    record_surge_alert,
    is_surge_alerted_recently,
    boost_queue_priority_by_anime,
    get_tracked_link_data,
    enqueue_pin,
    record_tracked_link,
    get_metadata,
    set_metadata,
)

logger = get_logger(__name__)

# Concurrency & Debounce guards
_RADAR_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_ALERT_CACHE: dict[str, float] = {}  # {code: unix_timestamp}
_LAST_TRIGGER_TIME = 0.0
_DEBOUNCE_INTERVAL = 300.0  # 5 minutes minimum between web-triggered checks
MAX_ALERTS_PER_CYCLE = 1    # Circuit breaker: max 1 alert per scan cycle
MAX_DAILY_VIRAL_ALERTS = VIRAL_MAX_DAILY_ALERTS  # Global daily cap across all pins
_GLOBAL_ALERT_MIN_INTERVAL = 1800.0  # At least 30 minutes between ANY two alerts sent to Telegram
_LAST_GLOBAL_ALERT_TIME = 0.0


def _init_alert_cache():
    """Populate in-memory alert cache from bot_metadata so cooldown survives restarts."""
    global _LAST_GLOBAL_ALERT_TIME
    try:
        raw = get_metadata("last_viral_alerts", "{}")
        if raw:
            data = json.loads(raw)
            with _CACHE_LOCK:
                for k, v in data.items():
                    _ALERT_CACHE[str(k).strip()] = float(v)
            logger.debug(f"[ViralRadar] Initialized alert cache with {len(_ALERT_CACHE)} entries.")
    except Exception as e:
        logger.debug(f"[ViralRadar] Could not load alert cache from metadata: {e}")

    try:
        raw_last = get_metadata("last_global_surge_alert_time", "0")
        if raw_last:
            _LAST_GLOBAL_ALERT_TIME = float(raw_last)
    except Exception:
        pass


# Seed alert cache on module import
_init_alert_cache()


def detect_viral_surges(
    window_hours: int = 24,
    min_visits: int = 5,
    check_cooldown: bool = True,
) -> list[dict]:
    """
    Scans bridge_visits and link_clicks for pins experiencing abnormal traffic surges.
    Returns a sorted list of candidate dictionaries ordered by surge_score DESC.
    """
    try:
        cutoff = (
            datetime.datetime.utcnow() - datetime.timedelta(hours=window_hours)
        ).strftime("%Y-%m-%d %H:%M:%S")

        with _get_conn() as conn:
            # Group visits by code
            rows = conn.execute(
                """
                SELECT bv.code, bv.anime_name, bv.title,
                       COUNT(bv.id) as total_visits,
                       COUNT(CASE WHEN bv.is_pinterest=1 THEN 1 END) as pinterest_visits,
                       MIN(bv.visited_at) as first_visit,
                       MAX(bv.visited_at) as last_visit
                FROM bridge_visits bv
                WHERE bv.visited_at >= ?
                  AND bv.code IS NOT NULL AND bv.code != ''
                GROUP BY bv.code
                HAVING total_visits >= ?
                """,
                (cutoff, min_visits),
            ).fetchall()

            if not rows:
                return []

            candidates = []
            for r in rows:
                code = r[0]
                anime_name = r[1] or "Anime"
                title = r[2] or "Anime Artwork"
                total_visits = r[3] or 0
                pinterest_visits = r[4] or 0
                first_visit_str = r[5]
                last_visit_str = r[6]

                # 1. Recency check: traffic must have arrived in the last 60 minutes
                try:
                    t_last = datetime.datetime.strptime(
                        last_visit_str, "%Y-%m-%d %H:%M:%S"
                    )
                    now_utc = datetime.datetime.utcnow()
                    if (now_utc - t_last).total_seconds() > 3600.0:
                        continue  # Stale / historical traffic, not an active surge
                except Exception:
                    continue

                # 2. Check cooldown if requested (in-memory fast cache first, then DB)
                if check_cooldown:
                    now_ts = time.time()
                    cooldown_secs = VIRAL_ALERT_COOLDOWN_HOURS * 3600
                    with _CACHE_LOCK:
                        last_ts = _ALERT_CACHE.get(code, 0.0)
                        if (now_ts - last_ts) < cooldown_secs:
                            continue

                    if is_surge_alerted_recently(
                        code, cooldown_hours=VIRAL_ALERT_COOLDOWN_HOURS
                    ):
                        with _CACHE_LOCK:
                            _ALERT_CACHE[code] = now_ts
                        continue

                # Query affiliate clicks for this code
                clicks_row = conn.execute(
                    """
                    SELECT COUNT(id) FROM link_clicks
                    WHERE code = ? AND clicked_at >= ?
                    """,
                    (code, cutoff),
                ).fetchone()
                clicks = clicks_row[0] if clicks_row else 0

                # Compute time span & velocity (visits per hour)
                try:
                    t_first = datetime.datetime.strptime(
                        first_visit_str, "%Y-%m-%d %H:%M:%S"
                    )
                    span_hours = max(0.5, (t_last - t_first).total_seconds() / 3600.0)
                except Exception:
                    span_hours = 1.0

                velocity = round(total_visits / span_hours, 1)

                # 3. Real Traffic Quality Filter:
                # Genuine Pinterest viral surge MUST have verified Pinterest referrals (>=2)
                # or actual consumer affiliate clicks (>=1).
                # Zero-Pinterest crawler/bot visits are strictly rejected.
                has_real_traffic = (
                    (pinterest_visits >= 2)
                    or (clicks >= 1 and total_visits >= 5)
                )
                if not has_real_traffic:
                    continue

                # Surge Score: Pinterest referral visits weighted x2.0, other x1.0, clicks x3.0
                external_visits = max(0, total_visits - pinterest_visits)
                surge_score = round(
                    (pinterest_visits * 2.0) + (external_visits * 1.0) + (clicks * 3.0),
                    1,
                )

                ctr = round((clicks / max(1, total_visits)) * 100, 1)

                candidates.append(
                    {
                        "code": code,
                        "anime_name": anime_name,
                        "title": title,
                        "total_visits": total_visits,
                        "pinterest_visits": pinterest_visits,
                        "external_visits": external_visits,
                        "clicks": clicks,
                        "ctr": ctr,
                        "velocity": velocity,
                        "surge_score": surge_score,
                    }
                )

            # Sort highest surge score first
            candidates.sort(key=lambda x: x["surge_score"], reverse=True)
            return candidates

    except Exception as e:
        err_msg = str(e)
        if "no such table: bridge_visits" in err_msg:
            try:
                from database import init_db
                init_db()
            except Exception:
                pass
            logger.debug("[ViralRadar] bridge_visits table initialized on demand.")
            return []
        logger.error(f"[ViralRadar] detect_viral_surges error: {e}")
        return []


def trigger_debounced_radar_check() -> bool:
    """
    Safely triggers a viral radar check from HTTP bridge traffic.
    Debounced to run at most once every 5 minutes and never spawns
    overlapping or runaway threads. Returns True if a check was scheduled.
    """
    global _LAST_TRIGGER_TIME
    now = time.time()
    if now - _LAST_TRIGGER_TIME < _DEBOUNCE_INTERVAL:
        return False

    if not _RADAR_LOCK.acquire(blocking=False):
        return False

    _LAST_TRIGGER_TIME = now

    def _worker():
        try:
            _execute_check_and_notify()
        except Exception as e:
            logger.error(f"[ViralRadar] Debounced worker error: {e}")
        finally:
            _RADAR_LOCK.release()

    threading.Thread(target=_worker, daemon=True, name="ViralRadarDebounced").start()
    return True


def check_and_notify_viral_surges(is_locked: bool = False) -> int:
    """
    Periodic check invoked by scheduler or bridge traffic trigger.
    Finds unalerted viral surges, saves them to database, and notifies admin via Telegram.
    Enforces concurrency lock, in-memory dedup, and a per-cycle alert cap.
    Returns the count of surges alerted.
    """
    if not VIRAL_RADAR_ENABLED:
        return 0

    if not is_locked:
        if not _RADAR_LOCK.acquire(blocking=False):
            logger.debug("[ViralRadar] Check already running in another thread, skipping.")
            return 0
        try:
            return _execute_check_and_notify()
        finally:
            _RADAR_LOCK.release()
    else:
        return _execute_check_and_notify()


def _execute_check_and_notify() -> int:
    """Internal execution logic running under _RADAR_LOCK."""
    global _LAST_GLOBAL_ALERT_TIME

    now_ts = time.time()

    # 1. Enforce global alert spacing (min 30 min between ANY two telegram alerts)
    if (now_ts - _LAST_GLOBAL_ALERT_TIME) < _GLOBAL_ALERT_MIN_INTERVAL:
        rem_sec = int(_GLOBAL_ALERT_MIN_INTERVAL - (now_ts - _LAST_GLOBAL_ALERT_TIME))
        logger.debug(
            f"[ViralRadar] Global alert spacing active ({rem_sec}s remaining). Skipping cycle."
        )
        return 0

    # 2. Enforce global daily alert cap (max 3 alerts / day)
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    daily_key = f"viral_alerts_daily_count_{today_str}"
    try:
        from database import get_metadata, set_metadata
        current_daily_alerts = int(get_metadata(daily_key, "0") or 0)
    except Exception:
        current_daily_alerts = 0

    if current_daily_alerts >= MAX_DAILY_VIRAL_ALERTS:
        logger.info(
            f"[ViralRadar] Reached MAX_DAILY_VIRAL_ALERTS ({MAX_DAILY_VIRAL_ALERTS}) for {today_str}. Halting surge alerts."
        )
        return 0

    candidates = detect_viral_surges(
        window_hours=24,
        min_visits=VIRAL_SURGE_MIN_VISITS,
        check_cooldown=True,
    )

    if not candidates:
        return 0

    alerted_count = 0
    cooldown_seconds = VIRAL_ALERT_COOLDOWN_HOURS * 3600

    for c in candidates:
        if alerted_count >= MAX_ALERTS_PER_CYCLE:
            logger.info(
                f"[ViralRadar] Reached MAX_ALERTS_PER_CYCLE ({MAX_ALERTS_PER_CYCLE}), deferring remaining candidates to next cycle."
            )
            break

        if current_daily_alerts >= MAX_DAILY_VIRAL_ALERTS:
            break

        code = c["code"]
        anime = c["anime_name"]
        title = c["title"]
        visits = c["total_visits"]
        pint_visits = c["pinterest_visits"]
        clicks = c["clicks"]
        ctr = c["ctr"]
        velocity = c["velocity"]
        score = c["surge_score"]

        # Double check in-memory cache under lock
        with _CACHE_LOCK:
            last_ts = _ALERT_CACHE.get(code, 0.0)
            if (now_ts - last_ts) < cooldown_seconds:
                continue

        # Record alert in DB first
        recorded = record_surge_alert(
            code=code,
            anime_name=anime,
            visits_count=visits,
            clicks_count=clicks,
            surge_score=score,
        )

        if not recorded:
            logger.warning(
                f"[ViralRadar] Skipping alert for '{code}' — database write failed."
            )
            continue

        # Update in-memory cache immediately to block any subsequent thread
        with _CACHE_LOCK:
            _ALERT_CACHE[code] = time.time()

        base_url = (APP_BASE_URL or "").rstrip("/")
        bridge_url = f"{base_url}/p/{code}" if base_url else f"https://your-bot.onrender.com/p/{code}"

        msg = (
            f"🚨 *VIRAL PIN SURGE DETECTED!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎨 *{anime}*\n"
            f"📌 Title: _{title[:90]}_\n"
            f"🔗 Bridge: `{code}`\n\n"
            f"📊 *Surge Metrics (Last 24h)*:\n"
            f"  • Bridge Visits: *{visits}* (from Pinterest: *{pint_visits}* 🎯)\n"
            f"  • Traffic Velocity: *{velocity}* visits/hr ⚡\n"
            f"  • Affiliate Clicks: *{clicks}* ({ctr}% CTR) 🛒\n"
            f"  • Surge Score: *{score}*\n\n"
            f"💡 *Actionable Opportunity*:\n"
            f"Pinterest traffic detected for this artwork.\n"
            f"Tap *Spinoff 2 Pins Now* to queue 2 fresh express variations at Priority #1!"
        )

        # Assemble inline keyboard buttons
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔥 Spinoff 2 Pins Now", callback_data=f"spinoff:{code}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "⚡ Express Priority #1", callback_data=f"boost:{code}"
                        ),
                        InlineKeyboardButton(
                            "📊 Pin Analytics", callback_data=f"analytics:{code}"
                        ),
                    ],
                    [
                        InlineKeyboardButton("🌐 View Bridge Page", url=bridge_url),
                        InlineKeyboardButton(
                            "🔕 Dismiss", callback_data=f"dismiss_surge:{code}"
                        ),
                    ],
                ]
            )

            from telegram_bot import notify_admin_with_keyboard

            notify_admin_with_keyboard(msg, reply_markup=keyboard)
            logger.info(
                f"[ViralRadar] Alert sent for viral code '{code}' ({anime}): {visits} visits, score={score}"
            )
            alerted_count += 1
            _LAST_GLOBAL_ALERT_TIME = time.time()
            current_daily_alerts += 1
            try:
                set_metadata("last_global_surge_alert_time", str(_LAST_GLOBAL_ALERT_TIME))
                set_metadata(daily_key, str(current_daily_alerts))
            except Exception:
                pass

            # Auto-spinoff if enabled in config
            if VIRAL_AUTO_SPINOFF:
                generate_spinoff_pins(code, count=2)

        except Exception as notify_err:
            logger.error(f"[ViralRadar] Failed to send Telegram alert: {notify_err}")

    return alerted_count


def generate_spinoff_pins(code: str, count: int = 2) -> dict:
    """
    Generates or promotes spinoff pins for a viral artwork.
    1. First attempts to boost existing matching pins in queue to priority=2 (Express).
    2. If fewer than count pins exist in queue, creates new distinct angle variations
       (e.g., AMOLED Phone Lockscreen vs Displate Room Decor) and enqueues them.
    """
    # 1. Lookup winning pin metadata
    link_data = get_tracked_link_data(code) or {}
    anime_name = link_data.get("anime_name") or "Anime"
    original_title = link_data.get("title") or f"{anime_name} Aesthetic Art"
    target_url = link_data.get("target_url") or ""
    image_url = link_data.get("image_url") or ""

    # Fallback to bridge_visits if tracked_links didn't have anime_name
    if not anime_name or anime_name == "Anime":
        try:
            with _get_conn() as conn:
                r = conn.execute(
                    "SELECT anime_name, title FROM bridge_visits WHERE code = ? LIMIT 1",
                    (code,),
                ).fetchone()
                if r:
                    anime_name = r[0] or anime_name
                    original_title = r[1] or original_title
        except Exception:
            pass

    # 2. Try boosting existing pins for this anime in queue
    boosted_count = boost_queue_priority_by_anime(
        anime_name=anime_name,
        new_priority=2,  # Express Priority (ahead of Priority 1 and 0)
        limit=count,
    )

    generated_count = 0
    today_str = (
        datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    ).strftime("%Y-%m-%d")

    # 3. If needed, generate fresh distinct angle variants
    needed = max(0, count - boosted_count)
    if needed > 0:
        from seo_matrix import (
            build_multi_intent_title,
            build_multi_intent_description,
            generate_multi_intent_tags,
        )

        # Predefined distinct angles
        angles = [
            {
                "theme": "AMOLED Phone Lockscreen 4K",
                "genre": "wallpaper",
                "desc_prefix": "Download pristine 4K AMOLED phone lockscreen anime aesthetic wallpaper. High resolution personal background.",
            },
            {
                "theme": "Aesthetic Room Decor & Wall Art",
                "genre": "decor",
                "desc_prefix": "Transform your bedroom and anime gaming setup with official metal wall art and collector figures.",
            },
            {
                "theme": "Collector Figures & Merch Edition",
                "genre": "merch",
                "desc_prefix": "Explore top-rated anime merchandise, collector statues, and official authentic figures.",
            },
        ]

        for i in range(needed):
            angle = angles[i % len(angles)]
            spinoff_title = build_multi_intent_title(
                f"{anime_name} {angle['theme']}",
                anime_name=anime_name,
                character_name="",
                genre=angle["genre"],
            )

            # Generate new unique tracking code for this spinoff
            short_id = uuid.uuid4().hex[:6]
            spinoff_code = f"sp_{short_id}"

            base_url = (APP_BASE_URL or "").rstrip("/")
            bridge_url = f"{base_url}/p/{spinoff_code}" if base_url else f"https://your-bot.onrender.com/p/{spinoff_code}"

            spinoff_desc = build_multi_intent_description(
                base_description=angle["desc_prefix"],
                anime_name=anime_name,
                character_name="",
                link_url=bridge_url,
            )

            tags = generate_multi_intent_tags(
                anime_name=anime_name,
                character_name="",
                genre=angle["genre"],
                target_count=13,
            )
            full_desc = f"{spinoff_desc}\n\n{tags}"

            # Ensure affiliate target URL exists
            if not target_url:
                tag = AMAZON_AFFILIATE_TAG or "animeasthet06-21"
                clean_q = anime_name.replace(" ", "+")
                target_url = f"https://www.amazon.in/s?k={clean_q}+anime+figure+merchandise&tag={tag}&sort=review-rank"

            # Register tracked link
            record_tracked_link(
                code=spinoff_code,
                target_url=target_url,
                anime_name=anime_name,
                title=spinoff_title,
                image_url=image_url,
            )

            # Enqueue with priority=2 (Express Priority)
            post_id = f"spinoff_{spinoff_code}"
            success = enqueue_pin(
                post_id=post_id,
                image_path="",  # Image URL will be used
                title=spinoff_title,
                description=full_desc,
                link=bridge_url,
                anime_name=anime_name,
                image_url=image_url,
                priority=2,  # EXPRESS PRIORITY
                scheduled_date=today_str,
                allow_duplicate=True,
            )
            if success:
                generated_count += 1
                logger.info(
                    f"[ViralRadar] Enqueued Express Spinoff #{i+1} for '{anime_name}' (code: {spinoff_code}, priority: 2)"
                )

    total_ready = boosted_count + generated_count
    logger.info(
        f"[ViralRadar] Spinoff execution for '{anime_name}': {boosted_count} boosted, {generated_count} generated (total: {total_ready})"
    )

    return {
        "code": code,
        "anime_name": anime_name,
        "boosted": boosted_count,
        "generated": generated_count,
        "total": total_ready,
    }


def format_radar_leaderboard(limit: int = 5) -> str:
    """
    Assembles a Markdown report for the Telegram /radar (or /viral) command.
    """
    candidates = detect_viral_surges(window_hours=24, min_visits=1, check_cooldown=False)

    lines = [
        "🚨 *Viral Pin Surge Radar*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not candidates:
        lines.extend(
            [
                "📡 *Status*: Radar Active & Scanning",
                "No pins have reached viral surge velocity in the last 24 hours.",
                "",
                f"• Threshold: *{VIRAL_SURGE_MIN_VISITS}* visits in 24h",
                "• All incoming traffic from Pinterest search is actively monitored.",
            ]
        )
        return "\n".join(lines)

    lines.append(f"🔥 *Top Trending Pins (Last 24h)*:\n")
    top_candidates = candidates[:limit]

    for idx, c in enumerate(top_candidates, start=1):
        status_badge = "🔥 SURGE" if c["total_visits"] >= VIRAL_SURGE_MIN_VISITS else "📈 Rising"
        lines.append(
            f"*{idx}. {c['anime_name']}* — {status_badge}\n"
            f"  • Code: `{c['code']}`\n"
            f"  • Visits: *{c['total_visits']}* ({c['pinterest_visits']} from Pinterest)\n"
            f"  • Velocity: *{c['velocity']}* visits/hr ⚡\n"
            f"  • Clicks: *{c['clicks']}* ({c['ctr']}% CTR) | Score: *{c['surge_score']}*\n"
        )

    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💡 *Tip*: Use `/spinoff <code>` to immediately create 2 express spinoff pins for any code!",
        ]
    )

    return "\n".join(lines)
