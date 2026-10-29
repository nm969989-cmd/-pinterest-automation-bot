"""
Smart Pin Scheduler
===================
- Posts 5 pins/day at human-like randomized IST times: 9 AM, 1 PM, 4 PM, 6 PM, 8 PM
- Each slot has a daily random jitter of +/-20 minutes (looks 100% human to Pinterest)
- Jitter is regenerated each day at midnight so times vary every day
- Priority: NEW images always post before BACKLOG images
- If 10 new images arrive -> 5 today, 5 scheduled across next days
- Multi-day queue is persisted in SQLite (survives restarts)
"""

import os
import re
import time
import random
import threading
import datetime
from collections import deque
from logger import get_logger
import config

logger = get_logger(__name__)

def _get_max_posts() -> int:
    """Dynamically get daily max posts, honoring in-memory config updates from /setmax."""
    return max(1, getattr(config, "MAX_POSTS_PER_DAY", 5))

MAX_POSTS_PER_DAY = _get_max_posts()

# Base IST posting times: 9:00 AM, 1:00 PM, 4:00 PM, 6:00 PM, 8:00 PM
# IST = UTC+5:30, so in UTC: 3:30, 7:30, 10:30, 12:30, 14:30
_BASE_POST_TIMES_UTC = [
    (3,  30),  # 09:00 AM IST
    (7,  30),  # 01:00 PM IST
    (10, 30),  # 04:00 PM IST
    (12, 30),  # 06:00 PM IST
    (14, 30),  # 08:00 PM IST
]

# Anti-bot jitter: max +/- minutes to randomize each slot
# Pinterest spam detection flags accounts that post at exact clockwork times.
_JITTER_MAX_MINUTES = 20

# ── Daily jitter state ────────────────────────────────────────────────────────
# Generated once per day. Stores (day_str, [(h_offset, m_offset), ...])
# PERSISTED to SQLite so Render restarts don't regenerate a different jitter
# and accidentally skip a scheduled slot.
_jitter_cache: tuple[str, list] = ("", [])


def _load_jitter_from_db(today: str):
    """Load persisted jitter offsets for today from the database (if they exist)."""
    try:
        from database import get_metadata
        raw = get_metadata(f"jitter_{today}", "")
        if raw:
            offsets = [int(x) for x in raw.split(",")]
            if len(offsets) == len(_BASE_POST_TIMES_UTC):
                return offsets
    except Exception:
        pass
    return None


def _save_jitter_to_db(today: str, offsets: list):
    """Persist today's jitter offsets to DB so they survive restarts."""
    try:
        from database import set_metadata
        set_metadata(f"jitter_{today}", ",".join(str(o) for o in offsets))
    except Exception:
        pass


def _get_daily_jitter() -> list[tuple[int, int]]:
    """
    Returns the list of jitter offsets (minutes) for each slot today.
    PERSISTS to SQLite so Render restarts reuse the same jitter — preventing
    slot times from shifting after a restart and missing a scheduled post.
    Re-generates fresh random offsets every new calendar day (IST).
    """
    global _jitter_cache
    today = _ist_now().strftime("%Y-%m-%d")
    if _jitter_cache[0] != today:
        # Try to load from DB first (survives Render restart)
        offsets = _load_jitter_from_db(today)
        if offsets is None:
            # First run today — generate new jitter and persist it
            offsets = [
                random.randint(-_JITTER_MAX_MINUTES, _JITTER_MAX_MINUTES)
                for _ in _BASE_POST_TIMES_UTC
            ]
            _save_jitter_to_db(today, offsets)
            logger.info(
                f"[Scheduler] NEW daily jitter generated & saved for {today}: "
                + ", ".join(f"{'+' if j >= 0 else ''}{j}min" for j in offsets)
            )
        else:
            logger.info(
                f"[Scheduler] Loaded persisted jitter for {today}: "
                + ", ".join(f"{'+' if j >= 0 else ''}{j}min" for j in offsets)
            )
        _jitter_cache = (today, offsets)
    return _jitter_cache[1]


def _get_jittered_times_utc() -> list[tuple[int, int]]:
    """
    Returns the 3 actual posting times (UTC) for today, with jitter applied.
    e.g. base 09:00 IST + 12min jitter = 09:12 IST = 03:42 UTC
    """
    jitters = _get_daily_jitter()
    result = []
    for i, (base_h, base_m) in enumerate(_BASE_POST_TIMES_UTC):
        total_minutes = base_h * 60 + base_m + jitters[i]
        # Clamp to valid time range (never go before midnight or after 23:59)
        total_minutes = max(0, min(23 * 60 + 59, total_minutes))
        result.append((total_minutes // 60, total_minutes % 60))
    return result


def _ist_now() -> datetime.datetime:
    """Current time in IST (UTC+5:30)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def _today_ist() -> str:
    return _ist_now().strftime("%Y-%m-%d")


def get_upcoming_slot_times(count: int = 5) -> list[str]:
    """
    Returns the next `count` exact scheduled posting slot times in IST.
    e.g. ['Today @ 06:17 PM IST (in ~3h 41m)', 'Tomorrow @ ~09:00 AM IST', ...]
    Used by the /queue Telegram command to display exact posting times.
    """
    now_utc = datetime.datetime.utcnow()
    now_ist = _ist_now()
    today_utc = now_utc.date()

    jittered_utc = _get_jittered_times_utc()
    slots = []

    # Check today's remaining jittered slots (converted to IST)
    for (h, m) in jittered_utc:
        slot_utc = datetime.datetime(today_utc.year, today_utc.month, today_utc.day, h, m)
        slot_ist = slot_utc + datetime.timedelta(hours=5, minutes=30)
        if slot_ist > now_ist:
            mins_away = int((slot_ist - now_ist).total_seconds() / 60)
            h_diff, m_diff = divmod(mins_away, 60)
            countdown = f"in ~{h_diff}h {m_diff}m" if h_diff > 0 else f"in ~{m_diff}m"
            slots.append(f"Today @ {slot_ist.strftime('%I:%M %p')} IST ({countdown})")

    # Fill remaining slots with upcoming future days
    day_offset = 1
    while len(slots) < count:
        target_date = now_ist.date() + datetime.timedelta(days=day_offset)
        prefix = "Tomorrow" if day_offset == 1 else target_date.strftime("%b %d")
        for (bh, bm) in _BASE_POST_TIMES_UTC:
            b_dt = datetime.datetime(
                target_date.year, target_date.month, target_date.day, bh, bm
            ) + datetime.timedelta(hours=5, minutes=30)
            slots.append(f"{prefix} @ ~{b_dt.strftime('%I:%M %p')} IST")
            if len(slots) >= count:
                break
        day_offset += 1

    return slots[:count]



def _assign_scheduled_date(queue_position: int) -> str:
    """
    Given a position in the queue (0-indexed), calculate which date to post.
    3 slots/day: position 0-2 → today, 3-5 → tomorrow, etc.
    """
    days_ahead = queue_position // _get_max_posts()
    target_date = _ist_now().date() + datetime.timedelta(days=days_ahead)
    return target_date.strftime("%Y-%m-%d")


def _dispatch_stock_uploads_async(image_path: str, title: str = "", caption: str = ""):
    """
    Safely dispatches image upload to configured stock photo platforms in a background thread.
    Zero impact on Pinterest posting. Skips platforms without credentials or if daily limit reached.
    """
    if not image_path or not os.path.isfile(image_path):
        return

    def _worker():
        try:
            from database import count_stock_posts_today, mark_stock_uploaded
            max_posts = int(os.getenv("STOCK_MAX_POSTS_PER_DAY", "5"))

            # Pre-upload watermark check: ensure 100% clean image before stock platform upload
            target_image_path = image_path
            try:
                from watermark_inpainter import verify_and_clean
                target_image_path = verify_and_clean(image_path)
            except Exception as _wm_err:
                logger.warning(f"[Stock Dispatch] Pre-upload watermark check error (non-fatal): {_wm_err}")

            filename = os.path.basename(target_image_path)
            logger.info(f"[Stock Dispatch] Checking stock platforms for {filename} (limit: {max_posts}/day)...")

            # 1. Shutterstock
            if os.getenv("SHUTTERSTOCK_MAKE_WEBHOOK_URL") or os.getenv("SHUTTERSTOCK_FTP_USER") or os.getenv("SHUTTERSTOCK_ACCESS_TOKEN"):
                if count_stock_posts_today("shutterstock") < max_posts:
                    try:
                        from shutterstock_uploader import upload_to_shutterstock
                        ok = upload_to_shutterstock(target_image_path, title=title, caption=caption)
                        mark_stock_uploaded("shutterstock", filename, title, "success" if ok else "failed")
                        logger.info(f"[Stock Dispatch] Shutterstock result: {'SUCCESS' if ok else 'FAILED'}")
                    except Exception as e:
                        logger.warning(f"[Stock Dispatch] Shutterstock error: {e}")
                else:
                    logger.info("[Stock Dispatch] Shutterstock daily limit reached.")

            # 2. Adobe Stock
            if os.getenv("ADOBE_MAKE_WEBHOOK_URL") or os.getenv("ADOBE_FTP_USER"):
                if count_stock_posts_today("adobe") < max_posts:
                    try:
                        from adobe_uploader import upload_to_adobe
                        ok = upload_to_adobe(target_image_path, title=title, caption=caption)
                        mark_stock_uploaded("adobe", filename, title, "success" if ok else "failed")
                        logger.info(f"[Stock Dispatch] Adobe Stock result: {'SUCCESS' if ok else 'FAILED'}")
                    except Exception as e:
                        logger.warning(f"[Stock Dispatch] Adobe Stock error: {e}")
                else:
                    logger.info("[Stock Dispatch] Adobe Stock daily limit reached.")

            # 3. Freepik Contributor
            if os.getenv("FREEPIK_FTP_USER") and os.getenv("FREEPIK_FTP_PASS"):
                if count_stock_posts_today("freepik") < max_posts:
                    try:
                        from freepik_uploader import upload_to_freepik
                        ok = upload_to_freepik(target_image_path, title=title, caption=caption)
                        mark_stock_uploaded("freepik", filename, title, "success" if ok else "failed")
                        logger.info(f"[Stock Dispatch] Freepik result: {'SUCCESS' if ok else 'FAILED'}")
                    except Exception as e:
                        logger.warning(f"[Stock Dispatch] Freepik error: {e}")
                else:
                    logger.info("[Stock Dispatch] Freepik daily limit reached.")

            # 4. Dreamstime
            if os.getenv("DREAMSTIME_FTP_USER") and os.getenv("DREAMSTIME_FTP_PASS"):
                if count_stock_posts_today("dreamstime") < max_posts:
                    try:
                        from dreamstime_uploader import upload_to_dreamstime
                        ok = upload_to_dreamstime(target_image_path, title=title, caption=caption)
                        mark_stock_uploaded("dreamstime", filename, title, "success" if ok else "failed")
                        logger.info(f"[Stock Dispatch] Dreamstime result: {'SUCCESS' if ok else 'FAILED'}")
                    except Exception as e:
                        logger.warning(f"[Stock Dispatch] Dreamstime error: {e}")
                else:
                    logger.info("[Stock Dispatch] Dreamstime daily limit reached.")

            # 5. Depositphotos
            if os.getenv("DEPOSITPHOTOS_FTP_USER") and os.getenv("DEPOSITPHOTOS_FTP_PASS"):
                if count_stock_posts_today("depositphotos") < max_posts:
                    try:
                        from depositphotos_uploader import upload_to_depositphotos
                        ok = upload_to_depositphotos(target_image_path, title=title, caption=caption)
                        mark_stock_uploaded("depositphotos", filename, title, "success" if ok else "failed")
                        logger.info(f"[Stock Dispatch] Depositphotos result: {'SUCCESS' if ok else 'FAILED'}")
                    except Exception as e:
                        logger.warning(f"[Stock Dispatch] Depositphotos error: {e}")
                else:
                    logger.info("[Stock Dispatch] Depositphotos daily limit reached.")

            # 6. 123RF
            if os.getenv("RF123_FTP_USER") and os.getenv("RF123_FTP_PASS"):
                if count_stock_posts_today("123rf") < max_posts:
                    try:
                        from rf123_uploader import upload_to_123rf
                        ok = upload_to_123rf(target_image_path, title=title, caption=caption)
                        mark_stock_uploaded("123rf", filename, title, "success" if ok else "failed")
                        logger.info(f"[Stock Dispatch] 123RF result: {'SUCCESS' if ok else 'FAILED'}")
                    except Exception as e:
                        logger.warning(f"[Stock Dispatch] 123RF error: {e}")
                else:
                    logger.info("[Stock Dispatch] 123RF daily limit reached.")

        except Exception as e:
            logger.warning(f"[Stock Dispatch] Worker exception: {e}")

    threading.Thread(target=_worker, name="StockDispatchThread", daemon=True).start()


class PinScheduler:
    """
    Smart scheduler with priority queue and time-slot posting.

    Queue priority:
      priority=1  →  NEW image from channel  (always first)
      priority=0  →  BACKLOG image           (only when no new pending)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._slots_fired_store: set = set()
        self.is_running = False
        self.thread = None
        # In-memory fallback for tasks not yet persisted to DB
        self._mem_queue: deque = deque()

    # ── Public API ────────────────────────────────────────────────────────────

    def add_to_queue(self, task_func, **kwargs):
        """
        Legacy API: adds task to in-memory queue (used by approval callback).
        For normal flow, use enqueue_pin() in database.py directly.
        """
        with self._lock:
            self._mem_queue.append((task_func, kwargs))
        logger.info(f"[Scheduler] In-memory task queued. Total: {len(self._mem_queue)}")

    def enqueue_new_image(self, post_id: str, image_path: str, title: str,
                          description: str, link: str, anime_name: str,
                          image_url: str = "", board_id: str = "",
                          sha256: str = "", phash: str = "",
                          is_organic: int = 0):
        """
        Add a NEW image (priority=1) from the Telegram channel.
        Automatically calculates the correct posting date based on queue depth.
        is_organic=1 marks an 80/20 organic trust pin (no affiliate link).
        """
        from database import enqueue_pin, get_queue_counts, count_posts_today

        with self._lock:
            counts = get_queue_counts()
            # Only count new-priority items for today's slot calculation
            new_in_queue = counts["new"]
            today_posted = count_posts_today(_today_ist())
            # How many slots are left today?
            slots_left_today = _get_max_posts() - today_posted - new_in_queue

            if slots_left_today > 0:
                sched_date = _today_ist()
            else:
                # Calculate which future day this should go to
                queue_position = new_in_queue  # 0-indexed position
                sched_date = _assign_scheduled_date(queue_position)

            added = enqueue_pin(
                post_id=post_id, image_path=image_path, title=title,
                description=description, link=link, anime_name=anime_name,
                image_url=image_url, board_id=board_id, priority=1,
                scheduled_date=sched_date,
                sha256=sha256, phash=phash,
                is_organic=is_organic
            )
            if added:
                org_label = " [ORGANIC 🌿]" if is_organic else ""
                logger.info(
                    f"[Scheduler] NEW pin queued for {sched_date}{org_label}: '{title}' "
                    f"(queue: {counts['new']+1} new, {counts['backlog']} backlog)"
                )
            return added

    def enqueue_backlog_image(self, post_id: str, image_path: str, title: str,
                               description: str, link: str, anime_name: str,
                               image_url: str = "", board_id: str = "",
                               sha256: str = "", phash: str = "",
                               is_organic: int = 0):
        """
        Add a BACKLOG image (priority=0). Only posts when no new images pending.
        Always schedules for a future date to avoid competing with new images today.
        """
        from database import enqueue_pin, get_queue_counts

        with self._lock:
            counts = get_queue_counts()
            # Backlog always goes after all new images
            backlog_position = counts["new"] + counts["backlog"]
            sched_date = _assign_scheduled_date(backlog_position)

            added = enqueue_pin(
                post_id=post_id, image_path=image_path, title=title,
                description=description, link=link, anime_name=anime_name,
                image_url=image_url, board_id=board_id, priority=0,
                scheduled_date=sched_date,
                sha256=sha256, phash=phash,
                is_organic=is_organic
            )
            if added:
                org_label = " [ORGANIC 🌿]" if is_organic else ""
                logger.info(
                    f"[Scheduler] BACKLOG pin queued for {sched_date}{org_label}: '{title}'"
                )
            return added

    @property
    def queue_size(self):
        from database import get_queue_counts
        counts = get_queue_counts()
        return counts["total"] + len(self._mem_queue)

    # ── Internal worker ───────────────────────────────────────────────────────

    def _should_post_now(self) -> bool:
        """
        Returns True if current UTC time matches one of the 3 daily posting slots.
        Slot window: fires within a ±6 minute window of the target time.
        Wider window ensures the slot fires even if bot is briefly slow/restarting.
        """
        now = datetime.datetime.utcnow()
        for (h, m) in _get_jittered_times_utc():
            slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
            diff = abs((now - slot_start).total_seconds())
            if diff <= 360:  # within 6 minutes of slot
                return True
        return False

    def _minutes_to_next_slot(self) -> int:
        """Returns minutes until the next jittered posting slot (today or tomorrow)."""
        now = datetime.datetime.utcnow()
        today = now.date()
        jittered = _get_jittered_times_utc()
        candidates = []
        for (h, m) in jittered:
            slot = datetime.datetime(today.year, today.month, today.day, h, m)
            if slot > now:
                candidates.append(slot)
        # Also check tomorrow's first base slot (jitter not yet known for tomorrow)
        tomorrow = today + datetime.timedelta(days=1)
        h0, m0 = _BASE_POST_TIMES_UTC[0]
        candidates.append(datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, h0, m0))
        next_slot = min(candidates)
        return max(1, int((next_slot - now).total_seconds() / 60))

    # ── Post notification ─────────────────────────────────────────────────────

    def _notify_pin_posted(self, title: str, anime_name: str, link: str,
                            image_path: str, pin_type: str,
                            posted_today: int, time_ist: str,
                            arena_ok=None, arena_url: str = "",
                            bluesky_ok=None, bluesky_url: str = "",
                            raindrop_ok=None, raindrop_url: str = "",
                            mastodon_ok=None, mastodon_url: str = "",
                            pixelfed_ok=None, pixelfed_url: str = "",
                            freeimage_ok=None, freeimage_url: str = "",
                            imghippo_ok=None, imghippo_url: str = "",
                            wallhaven_ok=None, wallhaven_url: str = "",
                            deviantart_ok=None, deviantart_url: str = "",
                            imgbb_ok=None, imgbb_url: str = "",
                            imagekit_ok=None, imagekit_url: str = "",
                            imgpile_ok=None, imgpile_url: str = "",
                            pin_live: bool = True,

                            image_url: str = ""):
        """
        Send a Telegram notification to admin immediately after a pin is posted.

        pin_live=False → Make.com accepted the webhook but the pin is NOT yet
        confirmed on Pinterest; forwarded so the admin header can say "queue".
        """
        try:
            if not image_url or not image_url.startswith(("http://", "https://")):
                from database import get_uploaded_file_image_url
                fn = os.path.basename(image_path) if image_path else ""
                if fn:
                    image_url = get_uploaded_file_image_url(fn) or image_url
            from telegram_bot import notify_admin_pin_posted
            notify_admin_pin_posted(
                title=title,
                anime_name=anime_name,
                link=link,
                image_path=image_path,
                pin_type=pin_type,
                posted_today=posted_today,
                max_today=_get_max_posts(),
                time_ist=time_ist,
                arena_ok=arena_ok,
                arena_url=arena_url,
                bluesky_ok=bluesky_ok,
                bluesky_url=bluesky_url,
                raindrop_ok=raindrop_ok,
                raindrop_url=raindrop_url,
                mastodon_ok=mastodon_ok,
                mastodon_url=mastodon_url,
                pixelfed_ok=pixelfed_ok,
                pixelfed_url=pixelfed_url,
                freeimage_ok=freeimage_ok,
                freeimage_url=freeimage_url,
                imghippo_ok=imghippo_ok,
                imghippo_url=imghippo_url,
                wallhaven_ok=wallhaven_ok,
                wallhaven_url=wallhaven_url,
                deviantart_ok=deviantart_ok,
                deviantart_url=deviantart_url,
                imgbb_ok=imgbb_ok,
                imgbb_url=imgbb_url,
                imagekit_ok=imagekit_ok,
                imagekit_url=imagekit_url,
                imgpile_ok=imgpile_ok,
                imgpile_url=imgpile_url,
                pin_live=pin_live,

                image_url=image_url,
            )
        except Exception as e:
            logger.warning(f"[Scheduler] Notification failed (non-critical): {e}")


    def _spawn_periodic_jobs(self, today_ist: str, radar_due: bool):
        """Run housekeeping jobs on a side thread; skip if the previous run is still going."""
        t = getattr(self, "_periodic_thread", None)
        if t is not None and t.is_alive():
            return
        self._periodic_thread = threading.Thread(
            target=self._run_periodic_jobs, args=(today_ist, radar_due),
            name="PeriodicJobsThread", daemon=True,
        )
        self._periodic_thread.start()

    @staticmethod
    def _run_periodic_jobs(today_ist: str, radar_due: bool):
        def _safe(label, fn):
            try:
                fn()
            except Exception as err:
                logger.debug(f"[Scheduler] {label} error (non-critical): {err}")

        def _digest():
            from daily_digest import check_and_send_scheduled_digest
            check_and_send_scheduled_digest()

        def _radar():
            from viral_radar import check_and_notify_viral_surges
            check_and_notify_viral_surges()

        def _healer():
            from link_healer import check_and_run_autonomous_healer_cycle
            check_and_run_autonomous_healer_cycle()

        def _ab():
            from ab_tester import check_and_decide_ab_tests
            check_and_decide_ab_tests()

        def _weekly():
            from weekly_report import check_and_send_weekly_report
            check_and_send_weekly_report()

        def _revenue():
            from revenue_tracker import refresh_revenue_log
            from database import get_metadata, set_metadata
            key = f"rev_refresh_{today_ist}"
            if not get_metadata(key, ""):
                refresh_revenue_log()
                set_metadata(key, "done")

        def _reviver():
            from ghost_pin_reviver import check_and_run_daily_reviver
            check_and_run_daily_reviver()

        _safe("Daily digest check", _digest)
        if radar_due:
            _safe("Viral radar check", _radar)
        _safe("Autonomous stock healer check", _healer)
        _safe("A/B check", _ab)
        _safe("Weekly report check", _weekly)
        _safe("Revenue refresh", _revenue)
        _safe("Ghost reviver", _reviver)

    def _worker_loop(self):
        """Supervisor: an unexpected exception must not kill the posting thread."""
        while self.is_running:
            try:
                self._worker_loop_inner()
                return  # inner loop only returns once is_running is False
            except Exception as e:
                logger.error(
                    f"[Scheduler] Worker loop crashed, restarting in 15s: {e}",
                    exc_info=True,
                )
                time.sleep(15)

    def _worker_loop_inner(self):
        from database import (get_next_queued_pin, remove_queued_pin,
                               count_posts_today, mark_file_uploaded,
                               is_file_uploaded, is_image_url_uploaded,
                               increment_retry_count, update_pin_image_path,
                               count_posts_on_utc_date, move_to_dead_letter)
        from pinterest_uploader import upload_to_pinterest

        self.is_running = True
        # Log today's actual jittered posting times at startup
        jittered = _get_jittered_times_utc()
        ist_times = []
        for (h_utc, m_utc) in jittered:
            total = h_utc * 60 + m_utc + 5 * 60 + 30  # UTC -> IST
            ist_times.append(f"{(total // 60) % 24:02d}:{total % 60:02d}")
        logger.info(
            f"[Scheduler] Started. Max {_get_max_posts()} pins/day. "
            f"Today's jittered IST slots: {', '.join(ist_times)} "
            f"(base: 09:00, 13:00, 16:00, 18:00, 20:00 +/- up to {_JITTER_MAX_MINUTES}min)"
        )
        _last_fired_slot = None
        # Tracks (h, m, today_ist) so no slot can ever fire twice. Lives on the instance
        # so the supervisor restarting this loop after a crash cannot re-fire a slot.
        _slots_fired_today: set = self._slots_fired_store
        _pending_catchup_count = 0  # How many missed slots to post with safe anti-burst pacing
        _last_pin_post_time: float = 0.0  # Epoch timestamp of last post (for human pacing)
        _next_catchup_delay: float = 15 * 60 + random.randint(-180, 180)  # Safe 12-18 min interval

        # ── Make.com webhook health check ─────────────────────────────────────
        # Run once at startup, then every 6 hours, so a deactivated scenario
        # is caught BEFORE it causes a full day of missed posts.
        _MAKE_HEALTH_INTERVAL = 6 * 3600  # 6 hours in seconds
        _last_make_health_check: float = 0.0  # Force an immediate check on first loop pass
        _last_viral_radar_check: float = time.time()  # Wait 2h after startup before periodic viral radar check
        _last_recovery_alert_ts: float = 0.0  # Track timestamp of last recovery alert to prevent spam
        _last_recovery_alert_key: str = ""    # Track date & slot count of last recovery alert

        # ── Full startup schedule recovery ────────────────────────────────────
        # On every restart (Render deploy, spin-down, crash), we check ALL slots
        # that were scheduled for today (IST) and count how many posts were actually made.
        # Note: All 5 daily IST slots (09:00..20:00 IST = 03:30..14:30 UTC) fall on the
        # SAME calendar date in UTC as in IST. Anchoring slot_utc_dt to today_ist_date
        # ensures that between 00:00 AM IST and 05:30 AM IST (18:30..23:59 UTC of the
        # previous UTC date), today's IST slots are correctly seen as in the FUTURE
        # (0 slots passed) rather than falsely counting yesterday's 5 slots as missed.
        now_startup = datetime.datetime.utcnow()
        today_ist_startup = _today_ist()
        today_ist_date = datetime.date.fromisoformat(today_ist_startup)
        jittered_startup = _get_jittered_times_utc()
        today_posted_startup = count_posts_today(today_ist_startup)

        slots_passed_today = 0
        for (h, m) in jittered_startup:
            slot_utc_dt = datetime.datetime(
                today_ist_date.year, today_ist_date.month, today_ist_date.day, h, m
            )
            secs_past = (now_startup - slot_utc_dt).total_seconds()
            if secs_past > 360:  # slot passed and outside normal 6-min fire window
                slots_passed_today += 1

        # How many posts SHOULD have been made by now?
        expected_posts_by_now = min(slots_passed_today, _get_max_posts())
        missed_posts = max(0, expected_posts_by_now - today_posted_startup)

        if missed_posts > 0:
            logger.warning(
                f"[Scheduler] RECOVERY: {slots_passed_today} slot(s) passed today, "
                f"but only {today_posted_startup} post(s) made. "
                f"Queuing {missed_posts} catch-up post(s) with anti-burst pacing (12-18 min intervals)."
            )
            _pending_catchup_count = missed_posts
            try:
                from telegram_bot import notify_admin
                notify_admin(
                    f"⚡ *Schedule Recovery Triggered*\n"
                    f"Bot restarted and detected {missed_posts} missed post(s) today.\n"
                    f"Posted so far: {today_posted_startup}/{expected_posts_by_now} expected.\n"
                    f"🛡️ *Anti-Burst Pacing Active*: {missed_posts} pin(s) will post safely spaced 12–18 mins apart to protect accounts."
                )
            except Exception:
                pass
        else:
            if slots_passed_today > 0:
                logger.info(
                    f"[Scheduler] Schedule healthy on startup: "
                    f"{today_posted_startup}/{slots_passed_today} expected post(s) completed. No catch-up needed."
                )
            else:
                logger.info("[Scheduler] No slots have passed yet today. Schedule on track.")
        # ─────────────────────────────────────────────────────────────────────

        # Track when we last ran a 'live heartbeat' check (every 30 min)
        _last_heartbeat_check = now_startup

        while self.is_running:
            now = datetime.datetime.utcnow()
            today_ist = _today_ist()

            # ── Make.com webhook health check (every 6 hours) ─────────────────
            now_ts_loop = time.time()
            if now_ts_loop - _last_make_health_check >= _MAKE_HEALTH_INTERVAL:
                _last_make_health_check = now_ts_loop
                try:
                    from pinterest_uploader import check_make_health
                    check_make_health()
                except Exception as _hc_err:
                    logger.warning(f"[Scheduler] Make.com health check error: {_hc_err}")

            # ── Live heartbeat: check if schedule is behind every 30 min ──────
            # Even without a restart, a slot can silently fail (Pinterest API
            # timeout, image error, etc.) and the bot may not retry it.
            # Every 30 minutes we compare posts made vs slots passed. If behind,
            # we immediately trigger a recovery post. If on schedule, skip.
            mins_since_heartbeat = (now - _last_heartbeat_check).total_seconds() / 60
            if mins_since_heartbeat >= 30:
                _last_heartbeat_check = now

                # ── Trigger organic human browsing & activity simulation ──────
                try:
                    from human_simulator import run_organic_simulation
                    threading.Thread(target=run_organic_simulation, name="HumanSimThread", daemon=True).start()
                except Exception as _sim_e:
                    logger.debug(f"[Scheduler] Human simulation thread spawn error: {_sim_e}")

                today_posted_hb = count_posts_today(today_ist)
                # Anchor slot UTC timestamps to today_ist_date so both slot counting
                # and post counting operate on the same IST day across midnight.
                today_ist_date_hb = datetime.date.fromisoformat(today_ist)
                slots_passed_hb = sum(
                    1 for (h, m) in _get_jittered_times_utc()
                    if (now - datetime.datetime(
                        today_ist_date_hb.year, today_ist_date_hb.month,
                        today_ist_date_hb.day, h, m
                    )).total_seconds() > 360
                )
                expected_hb = min(slots_passed_hb, _get_max_posts())
                missed_hb = max(0, expected_hb - today_posted_hb)
                recovery_alert_key = f"{today_ist}_{expected_hb}"
                if missed_hb > 0 and _pending_catchup_count == 0:
                    logger.warning(
                        f"[Scheduler] LIVE RECOVERY: Schedule drifted — "
                        f"{today_posted_hb}/{expected_hb} expected posts made. "
                        f"Queuing {missed_hb} catch-up post(s) with anti-burst pacing (12-18 min intervals)."
                    )
                    _pending_catchup_count = missed_hb
                    now_ts_recovery = time.time()
                    # Suppress repeated Telegram alert spam (only alert once per expected count or once every 4 hours)
                    if recovery_alert_key != _last_recovery_alert_key or (now_ts_recovery - _last_recovery_alert_ts) > 14400:
                        _last_recovery_alert_key = recovery_alert_key
                        _last_recovery_alert_ts = now_ts_recovery
                        try:
                            from telegram_bot import notify_admin
                            notify_admin(
                                f"⚡ *Live Schedule Recovery*\n"
                                f"Detected {missed_hb} missed post(s) mid-day.\n"
                                f"Posted: {today_posted_hb} | Expected by now: {expected_hb}\n"
                                f"🛡️ *Anti-Burst Pacing Active*: Catch-up pins spaced 12–18 mins apart."
                            )
                        except Exception as _notify_err:
                            logger.warning(f"[Scheduler] notify_admin failed during heartbeat recovery: {_notify_err}")
                    else:
                        logger.info(f"[Scheduler] Live recovery in progress — suppressed duplicate Telegram alert ({recovery_alert_key}).")
                elif missed_hb == 0 and slots_passed_hb > 0:
                    logger.info(
                        f"[Scheduler] Heartbeat OK — schedule on track: "
                        f"{today_posted_hb}/{expected_hb} posts. No action needed."
                    )
            # ─────────────────────────────────────────────────────────────────

            # Periodic jobs (digest, radar, healer, A/B, weekly report, revenue, reviver) run on a side thread so slow HTTP calls can never delay a posting slot.
            now_ts_radar = time.time()
            radar_due = (now_ts_radar - _last_viral_radar_check) >= 7200.0  # 2 hours
            if radar_due:
                _last_viral_radar_check = now_ts_radar
            self._spawn_periodic_jobs(today_ist, radar_due)

            # ── Check if it's a posting time slot (with today's jitter applied) ──
            current_slot = None
            jittered_times = _get_jittered_times_utc()
            for (h, m) in jittered_times:
                slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = abs((now - slot_start).total_seconds())
                if diff <= 360:  # within 6 minutes of jittered slot (wider = safer)
                    current_slot = (h, m)
                    break

            slot_key = (current_slot[0], current_slot[1], today_ist) if current_slot else None
            is_regular_slot = bool(
                current_slot
                and current_slot != _last_fired_slot
                and slot_key not in _slots_fired_today
            )
            should_post = False
            now_ts = time.time()
            elapsed_since_last_post = (now_ts - _last_pin_post_time) if _last_pin_post_time > 0 else 999999.0

            if is_regular_slot:
                should_post = True
                _last_fired_slot = current_slot
                _slots_fired_today.add(slot_key)
                # Drop entries from earlier days so the set cannot grow without bound.
                _slots_fired_today.intersection_update({k for k in _slots_fired_today if k[2] == today_ist})
            elif _pending_catchup_count > 0:
                # Catch-up post: enforce 12–18 min interval since last post to prevent rapid-fire burst bans
                if elapsed_since_last_post >= _next_catchup_delay:
                    should_post = True
                    _pending_catchup_count -= 1
                    # Choose a new randomized delay for subsequent catch-up posts (12-18 min)
                    _next_catchup_delay = 15 * 60 + random.randint(-180, 180)
                    logger.info(
                        f"[Scheduler] Anti-burst catch-up pacing cleared ({int(elapsed_since_last_post/60)}m elapsed). "
                        f"Posting 1 recovery pin ({_pending_catchup_count} remaining in recovery queue)."
                    )
                else:
                    rem_mins = max(1, int((_next_catchup_delay - elapsed_since_last_post) / 60))
                    # Log periodically (approx every 3 min) to keep logs informative but clean
                    if int(elapsed_since_last_post) % 180 < 35:
                        logger.info(
                            f"[Scheduler] Anti-burst pacing active: {rem_mins}m remaining before next catch-up pin "
                            f"({_pending_catchup_count} remaining in recovery queue)."
                        )

            # Trigger posting for: (a) normal slot OR (b) paced catch-up slot
            if should_post:
                display_slot = current_slot or _last_fired_slot or (0, 0)
                today_posted = count_posts_today(today_ist)

                # Convert UTC slot to IST for display
                slot_ist_total = display_slot[0] * 60 + display_slot[1] + 5 * 60 + 30
                slot_ist_h = (slot_ist_total // 60) % 24
                slot_ist_m = slot_ist_total % 60
                logger.info(
                    f"[Scheduler] Slot fired: {slot_ist_h:02d}:{slot_ist_m:02d} IST (jittered) | "
                    f"Posted today: {today_posted}/{_get_max_posts()}"
                )

                if today_posted >= _get_max_posts():
                    logger.info("[Scheduler] Daily limit reached. Skipping slot.")
                else:
                    # Try DB queue first (priority: new > backlog)
                    pin = get_next_queued_pin(today_ist)

                    if pin:
                        # ── Triple duplicate check (Filename, URL, and Visual pHash/SHA-256) ─
                        from database import is_image_duplicate, is_intentional_repin
                        filename = pin["image_path"].split("/")[-1].split("\\")[-1] if pin["image_path"] else ""
                        is_repin = is_intentional_repin(pin.get("post_id", ""))
                        is_dup = False
                        dup_reason = ""

                        if not is_repin:
                            if is_file_uploaded(filename):
                                is_dup, dup_reason = True, f"Filename '{filename}' already uploaded"
                            elif is_image_url_uploaded(pin.get("image_url", "")):
                                is_dup, dup_reason = True, "Image URL already uploaded"
                            else:
                                is_dup, dup_reason = is_image_duplicate(
                                    filepath=pin.get("image_path", ""),
                                    sha256=pin.get("sha256", ""),
                                    phash=pin.get("phash", ""),
                                    exclude_queue_id=pin["id"]
                                )

                        if is_dup:
                            logger.info(f"[Scheduler] Duplicate detected, removing from queue: '{pin['title']}' ({dup_reason})")
                            remove_queued_pin(pin["id"])
                        else:
                            logger.info(
                                f"[Scheduler] Posting {'NEW' if pin['priority']==1 else 'BACKLOG'} pin: "
                                f"'{pin['title']}'"
                            )

                            # ── File resurrection (Render ephemeral FS fix) ───
                            # Render's free tier wipes the filesystem on every
                            # restart/spin-down. If the image file is gone but
                            # the queue row has a stored image_url, re-download
                            # it automatically.
                            # NEW IMAGES store a permanent Cloudinary/Catbox URL
                            # so resurrection always works. Old queue entries
                            # may still have Telegram CDN URLs (expire ~1h) —
                            # use /fixqueue to re-upload those stale entries.
                            image_path = pin["image_path"]
                            if not os.path.exists(image_path):
                                cdn_url = pin.get("image_url", "")
                                if cdn_url and cdn_url.startswith("http"):
                                    # Detect stale Telegram CDN URLs — these expire
                                    # in ~1h and CANNOT be re-downloaded. Drop
                                    # immediately instead of wasting 3 retry slots.
                                    is_stale_cdn = (
                                        "telesco.pe" in cdn_url or "/t.me/" in cdn_url
                                    )
                                    if is_stale_cdn:
                                        logger.error(
                                            f"[Scheduler] ❌ Dropping pin immediately — "
                                            f"Telegram CDN URL is expired (cannot re-download): "
                                            f"'{pin['title']}'. Use /fixqueue to prevent this."
                                        )
                                        remove_queued_pin(pin["id"])
                                        try:
                                            from telegram_bot import notify_admin
                                            notify_admin(
                                                f"⚠️ Pin dropped — Telegram CDN expired:\n"
                                                f"'{pin['title']}'\n"
                                                f"Anime: {pin['anime_name']}\n"
                                                f"Re-send the original image to re-queue it with a "
                                                f"permanent URL. Run /fixqueue after re-sending."
                                            )
                                        except Exception:
                                            pass
                                        image_path = None
                                    else:
                                        logger.warning(
                                            f"[Scheduler] Image file missing (Render FS wipe?): {image_path}\n"
                                            f"[Scheduler] Re-downloading from: {cdn_url}"
                                        )
                                        try:
                                            from telegram_listener import download_image
                                            from image_processor import process_image
                                            raw_name = os.path.splitext(os.path.basename(image_path))[0] if image_path else ""
                                            safe_name = raw_name if raw_name else f"resurrect_{pin['id']}"
                                            dl_path = download_image(cdn_url, safe_name)
                                            if dl_path:
                                                image_path = process_image(dl_path)
                                                update_pin_image_path(pin["id"], image_path)
                                                logger.info(
                                                    f"[Scheduler] Re-download success: {image_path}"
                                                )
                                            else:
                                                raise RuntimeError("download_image returned None")
                                        except Exception as re_err:
                                            logger.error(
                                                f"[Scheduler] Re-download failed for '{pin['title']}': {re_err}"
                                            )
                                            new_count = increment_retry_count(pin["id"])
                                            if new_count >= 3:
                                                move_to_dead_letter(pin, fail_reason=f"Image re-download failed 3x: {re_err}")
                                                remove_queued_pin(pin["id"])
                                                logger.error(
                                                    f"[Scheduler] Dropped pin after 3 failed re-downloads: '{pin['title']}'"
                                                )
                                                try:
                                                    from telegram_bot import notify_admin
                                                    notify_admin(
                                                        f"⚠️ Pin dead-lettered — image lost & CDN expired:\n"
                                                        f"*{pin['title']}*\n"
                                                        f"Anime: {pin['anime_name']}\n"
                                                        f"The Telegram CDN URL has expired. "
                                                        f"Re-send the image to re-queue it.\n"
                                                        f"📋 Check /deadqueue for full history."
                                                    )
                                                except Exception:
                                                    pass
                                            else:
                                                logger.warning(
                                                    f"[Scheduler] Will retry re-download next slot "
                                                    f"(attempt {new_count}/3): '{pin['title']}'"
                                                )
                                            # Skip this slot — don't attempt upload with no file
                                            image_path = None
                                else:
                                    logger.error(
                                        f"[Scheduler] Image file missing and no CDN URL stored. "
                                        f"Cannot recover pin: '{pin['title']}'. "
                                        f"Dropping after next retry cycle."
                                    )
                                    new_count = increment_retry_count(pin["id"])
                                    if new_count >= 3:
                                        # Without this the same unrecoverable pin is returned
                                        # first on every slot and blocks the whole queue.
                                        move_to_dead_letter(pin, fail_reason="Image file missing and no CDN URL stored")
                                        remove_queued_pin(pin["id"])
                                        logger.error(
                                            f"[Scheduler] Dead-lettered unrecoverable pin: '{pin['title']}'"
                                        )
                                    image_path = None

                            if image_path:
                                # Pre-upload watermark check: ensure 100% clean image before Pinterest upload
                                try:
                                    from watermark_inpainter import verify_and_clean
                                    image_path = verify_and_clean(image_path)
                                except Exception as _wm_err:
                                    logger.warning(f"[Scheduler] Pre-upload watermark check error (non-fatal): {_wm_err}")

                                # ── Anti-Shadowban 80/20 Guard & Link Tracker Injection ──
                                from database import should_next_pin_be_organic
                                is_org = pin.get("is_organic", 0)

                                if getattr(config, "ORGANIC_TRUST_ENABLED", True) and should_next_pin_be_organic(getattr(config, "ORGANIC_TRUST_RATIO", 0.20)):
                                    logger.info(
                                        f"[Scheduler] 🌿 Anti-Shadowban Guard: 4+ consecutive monetized pins reached! "
                                        f"Converting '{pin['title']}' to ORGANIC TRUST PIN."
                                    )
                                    is_org = 1
                                    pin["is_organic"] = 1
                                    pin["link"] = (
                                        getattr(config, "ORGANIC_PIN_LINK", "")
                                        or getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")
                                    )
                                    pin["description"] = re.sub(
                                        r'#ad\b|#affiliate\b|#sponsored\b', '', pin.get("description", "")
                                    ).strip()
                                elif not is_org and getattr(config, "APP_BASE_URL", ""):
                                    # Wrap direct Amazon links with tracker if not already wrapped
                                    if "amazon." in pin.get("link", "") and "/r/" not in pin.get("link", ""):
                                        try:
                                            from amazon_search import wrap_with_tracker
                                            pin["link"] = wrap_with_tracker(
                                                pin["link"],
                                                anime_name=pin.get("anime_name", ""),
                                                title=pin.get("title", ""),
                                                image_url=pin.get("image_url", ""),
                                            )
                                        except Exception as _wrap_err:
                                            logger.debug(f"[Scheduler] Tracker wrapping error (non-fatal): {_wrap_err}")

                                # Build alt_text for Pinterest SEO
                                alt_text = (
                                    f"{pin['anime_name']} anime art poster wallpaper "
                                    f"{pin['title'].replace('-', ' ')}"
                                )[:500]

                                pin_status = upload_to_pinterest(
                                    image_path=image_path,
                                    title=pin["title"],
                                    description=pin["description"],
                                    link=pin["link"],
                                    anime_name=pin["anime_name"],
                                    board_id=pin.get("board_id", ""),
                                    alt_text=alt_text,
                                    allow_duplicate=is_repin,
                                )
                                # Tri-state: "live"/"queued"/False. Both live+queued dequeue.
                                success = bool(pin_status)
                                pin_is_live = (pin_status == "live")
                                if success:
                                    is_org = pin.get("is_organic", 0)
                                    actual_fn = os.path.basename(image_path)
                                    orig_fn = pin["image_path"].split("/")[-1].split("\\")[-1] if pin.get("image_path") else ""
                                    mark_file_uploaded(
                                        filename=actual_fn,
                                        title=pin["title"],
                                        anime_name=pin["anime_name"],
                                        image_url=pin.get("image_url", ""),
                                        sha256=pin.get("sha256", ""),
                                        phash=pin.get("phash", ""),
                                        is_organic=is_org,
                                    )
                                    if orig_fn and orig_fn != actual_fn:
                                        mark_file_uploaded(
                                            filename=orig_fn,
                                            title=pin["title"],
                                            anime_name=pin["anime_name"],
                                            image_url=pin.get("image_url", ""),
                                            sha256=pin.get("sha256", ""),
                                            phash=pin.get("phash", ""),
                                            is_organic=is_org,
                                        )
                                    remove_queued_pin(pin["id"])
                                    # Immediately sync to JSONBin cloud so a Render restart/deploy
                                    # during or right after crossposting can NEVER re-queue this pin!
                                    try:
                                        from jsonbin_sync import save_cloud_state
                                        threading.Thread(
                                            target=save_cloud_state,
                                            daemon=True,
                                            name="ImmediatePostSyncThread",
                                        ).start()
                                    except Exception as _sync_err:
                                        logger.warning(f"[Scheduler] Immediate post-upload cloud sync error: {_sync_err}")
                                    now_ist = _ist_now().strftime("%I:%M %p")
                                    if is_org:
                                        pin_type = "ORGANIC 🌿"
                                    else:
                                        pin_type = "NEW" if pin["priority"] == 1 else "BACKLOG"
                                    counts_after = count_posts_today(today_ist)
                                    logger.info(
                                        f"[Scheduler] Pin posted ({pin_type}). Today: "
                                        f"{counts_after}/{_get_max_posts()}"
                                    )

                                    # ── Run cross-posts FIRST so results can be included in notification ──
                                    from crosspost_dispatcher import dispatch_all_crossposts
                                    try:
                                        cp_res = dispatch_all_crossposts(pin, image_path, pin_url=pin.get("pin_url", ""))
                                    except Exception as cp_err:
                                        # Never let a cross-post failure kill the scheduler
                                        # worker loop (the pin is already posted to
                                        # Pinterest at this point). Fall back to empty
                                        # results so the admin notification + the stock
                                        # dispatch below still run (BUG 3).
                                        logger.error(
                                            f"[Scheduler] Cross-post dispatch failed for "
                                            f"'{pin.get('title', '?')}': {cp_err}",
                                            exc_info=True,
                                        )
                                        cp_res = {}

                                    # ── Notify admin (after cross-posts so results are known) ────────────
                                    try:
                                        self._notify_pin_posted(
                                            title=pin["title"],
                                            anime_name=pin["anime_name"],
                                            link=pin["link"],
                                            image_path=image_path,
                                            pin_type=pin_type,
                                            posted_today=counts_after,
                                            time_ist=now_ist,
                                            pin_live=pin_is_live,
                                            image_url=pin.get("image_url", ""),
                                            arena_ok=cp_res.get("arena_ok"),
                                            arena_url=cp_res.get("arena_url", ""),
                                            bluesky_ok=cp_res.get("bluesky_ok"),
                                            bluesky_url=cp_res.get("bluesky_url", ""),
                                            raindrop_ok=cp_res.get("raindrop_ok"),
                                            raindrop_url=cp_res.get("raindrop_url", ""),
                                            mastodon_ok=cp_res.get("mastodon_ok"),
                                            mastodon_url=cp_res.get("mastodon_url", ""),
                                            pixelfed_ok=cp_res.get("pixelfed_ok"),
                                            pixelfed_url=cp_res.get("pixelfed_url", ""),
                                            freeimage_ok=cp_res.get("freeimage_ok"),
                                            freeimage_url=cp_res.get("freeimage_url", ""),
                                            imghippo_ok=cp_res.get("imghippo_ok"),
                                            imghippo_url=cp_res.get("imghippo_url", ""),
                                            wallhaven_ok=cp_res.get("wallhaven_ok"),
                                            wallhaven_url=cp_res.get("wallhaven_url", ""),
                                            deviantart_ok=cp_res.get("deviantart_ok"),
                                            deviantart_url=cp_res.get("deviantart_url", ""),
                                            imgbb_ok=cp_res.get("imgbb_ok"),
                                            imgbb_url=cp_res.get("imgbb_url", ""),
                                            imagekit_ok=cp_res.get("imagekit_ok"),
                                            imagekit_url=cp_res.get("imagekit_url", ""),
                                            imgpile_ok=cp_res.get("imgpile_ok"),
                                            imgpile_url=cp_res.get("imgpile_url", ""),
                                        )

                                    except Exception as _notify_err:
                                        # Keep the posting slot (and stock dispatch below)
                                        # alive even when admin notification breaks.
                                        logger.error(
                                            f"[Scheduler] Pin-posted notification failed for "
                                            f"'{pin.get('title', '?')}': {_notify_err}",
                                            exc_info=True,
                                        )

                                    # Auto-dispatch to configured stock photography platforms
                                    try:
                                        _dispatch_stock_uploads_async(
                                            image_path=image_path,
                                            title=pin["title"],
                                            caption=pin["anime_name"],
                                        )
                                    except Exception as _stock_err:
                                        # A stock-dispatch failure must never abort the
                                        # posting slot (BUG 3).
                                        logger.error(
                                            f"[Scheduler] Stock dispatch failed for "
                                            f"'{pin.get('title', '?')}': {_stock_err}",
                                            exc_info=True,
                                        )
                                    _last_pin_post_time = time.time()
                                else:
                                    # Auto-retry: move to dead-letter after 3 fails
                                    MAX_RETRIES = 3
                                    new_count = increment_retry_count(pin["id"])
                                    if new_count >= MAX_RETRIES:
                                        move_to_dead_letter(pin, fail_reason=f"Pinterest upload failed {MAX_RETRIES} times")
                                        remove_queued_pin(pin["id"])
                                        logger.error(
                                            f"[Scheduler] Pin dead-lettered after {MAX_RETRIES} upload failures: '{pin['title']}'"
                                        )
                                        try:
                                            from telegram_bot import notify_admin
                                            notify_admin(
                                                f"☠️ Pin dead-lettered after {MAX_RETRIES} failed "
                                                f"upload attempts:\n*{pin['title']}*\n"
                                                f"Anime: {pin['anime_name']}\n"
                                                f"📋 Review & retry via /deadqueue"
                                            )
                                        except Exception:
                                            pass
                                    else:
                                        logger.warning(
                                            f"[Scheduler] Upload failed (attempt {new_count}/{MAX_RETRIES}). "
                                            f"Pin stays in queue: '{pin['title']}'"
                                        )

                    elif self._mem_queue:
                        # Fallback: in-memory queue (approval mode)
                        with self._lock:
                            task_func, kwargs = self._mem_queue.popleft()
                        try:
                            task_func(**kwargs)
                            _last_pin_post_time = time.time()
                        except Exception as e:
                            logger.error(f"[Scheduler] In-memory task error: {e}")
                    else:
                        logger.info("[Scheduler] Queue empty at slot time. Nothing to post.")

            else:
                # Reset slot tracker when outside ALL jittered slot windows
                all_outside = not any(
                    abs((now - now.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds()) <= 360
                    for (h, m) in _get_jittered_times_utc()
                )
                if all_outside:
                    _last_fired_slot = None

            time.sleep(30)  # Check every 30 seconds

    def start(self):
        self.thread = _start_scheduler_thread(self.thread)
        mins = self._minutes_to_next_slot()
        logger.info(f"[Scheduler] Next posting slot in ~{mins} minutes.")

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)


def _start_scheduler_thread(old_thread=None):
    """Start (or restart) the PinScheduler worker thread idempotently.

    Returns the live Thread. Restarts a thread only when the old one is gone
    or failed — keeping it a fresh watchdog-safe factory (BUG 2 + BUG 3).
    """
    if scheduler.thread is not None and scheduler.thread.is_alive():
        return scheduler.thread
    if old_thread is not None and old_thread.is_alive():
        scheduler.thread = old_thread
        return old_thread
    scheduler.is_running = True
    thread = threading.Thread(
        target=scheduler._worker_loop, daemon=True, name="SchedulerWorker"
    )
    thread.start()
    scheduler.thread = thread
    return thread


# Global instance
scheduler = PinScheduler()
