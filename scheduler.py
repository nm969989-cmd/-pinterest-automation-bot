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
import time
import random
import threading
import datetime
from collections import deque
from logger import get_logger
from config import MAX_POSTS_PER_DAY

logger = get_logger(__name__)

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
    days_ahead = queue_position // MAX_POSTS_PER_DAY
    target_date = _ist_now().date() + datetime.timedelta(days=days_ahead)
    return target_date.strftime("%Y-%m-%d")


class PinScheduler:
    """
    Smart scheduler with priority queue and time-slot posting.

    Queue priority:
      priority=1  →  NEW image from channel  (always first)
      priority=0  →  BACKLOG image           (only when no new pending)
    """

    def __init__(self):
        self._lock = threading.Lock()
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
                          image_url: str = "", board_id: str = ""):
        """
        Add a NEW image (priority=1) from the Telegram channel.
        Automatically calculates the correct posting date based on queue depth.
        """
        from database import enqueue_pin, get_queue_counts, count_posts_today

        with self._lock:
            counts = get_queue_counts()
            # Only count new-priority items for today's slot calculation
            new_in_queue = counts["new"]
            today_posted = count_posts_today(_today_ist())
            # How many slots are left today?
            slots_left_today = MAX_POSTS_PER_DAY - today_posted - new_in_queue

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
                scheduled_date=sched_date
            )
            if added:
                logger.info(
                    f"[Scheduler] NEW pin queued for {sched_date}: '{title}' "
                    f"(queue: {counts['new']+1} new, {counts['backlog']} backlog)"
                )
            return added

    def enqueue_backlog_image(self, post_id: str, image_path: str, title: str,
                               description: str, link: str, anime_name: str,
                               image_url: str = "", board_id: str = ""):
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
                scheduled_date=sched_date
            )
            if added:
                logger.info(
                    f"[Scheduler] BACKLOG pin queued for {sched_date}: '{title}'"
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
                            posted_today: int, time_ist: str):
        """
        Send a Telegram notification to admin immediately after a pin is posted.
        Uses the existing Telegram bot — completely FREE, no API limits at 3/day.
        """
        try:
            from telegram_bot import notify_admin_pin_posted
            notify_admin_pin_posted(
                title=title,
                anime_name=anime_name,
                link=link,
                image_path=image_path,
                pin_type=pin_type,
                posted_today=posted_today,
                max_today=MAX_POSTS_PER_DAY,
                time_ist=time_ist,
            )
        except Exception as e:
            logger.warning(f"[Scheduler] Notification failed (non-critical): {e}")

    def _worker_loop(self):
        from database import (get_next_queued_pin, remove_queued_pin,
                               count_posts_today, mark_file_uploaded,
                               is_file_uploaded, is_image_url_uploaded,
                               increment_retry_count, update_pin_image_path)
        from pinterest_uploader import upload_to_pinterest

        self.is_running = True
        # Log today's actual jittered posting times at startup
        jittered = _get_jittered_times_utc()
        ist_times = []
        for (h_utc, m_utc) in jittered:
            total = h_utc * 60 + m_utc + 5 * 60 + 30  # UTC -> IST
            ist_times.append(f"{(total // 60) % 24:02d}:{total % 60:02d}")
        logger.info(
            f"[Scheduler] Started. Max {MAX_POSTS_PER_DAY} pins/day. "
            f"Today's jittered IST slots: {', '.join(ist_times)} "
            f"(base: 09:00, 13:00, 16:00, 18:00, 20:00 +/- up to {_JITTER_MAX_MINUTES}min)"
        )
        _last_fired_slot = None
        _pending_catchup_count = 0  # How many missed slots to immediately post

        # ── Full startup schedule recovery ────────────────────────────────────
        # On every restart (Render deploy, spin-down, crash), we check ALL slots
        # that were scheduled for today and count how many posts were actually made.
        # If the bot missed N slots while offline, we queue N immediate catch-up posts.
        #
        # Guards:
        #   - Only fires for slots > 6 min in the past (outside normal fire window)
        #   - Counts today's actual DB posts vs expected posts by now
        #   - Never exceeds MAX_POSTS_PER_DAY cap
        now_startup = datetime.datetime.utcnow()
        today_ist_startup = _today_ist()
        jittered_startup = _get_jittered_times_utc()
        today_posted_startup = count_posts_today(today_ist_startup)

        # Count how many slots have fully passed today (beyond the 6-min fire window)
        slots_passed_today = 0
        for (h, m) in jittered_startup:
            slot_start = now_startup.replace(hour=h, minute=m, second=0, microsecond=0)
            secs_past = (now_startup - slot_start).total_seconds()
            if secs_past > 360:  # slot passed and outside normal 6-min fire window
                slots_passed_today += 1

        # How many posts SHOULD have been made by now?
        expected_posts_by_now = min(slots_passed_today, MAX_POSTS_PER_DAY)
        missed_posts = max(0, expected_posts_by_now - today_posted_startup)

        if missed_posts > 0:
            logger.warning(
                f"[Scheduler] RECOVERY: {slots_passed_today} slot(s) passed today, "
                f"but only {today_posted_startup} post(s) made. "
                f"Will immediately catch up with {missed_posts} post(s)."
            )
            _pending_catchup_count = missed_posts
            try:
                from telegram_bot import notify_admin
                notify_admin(
                    f"⚡ Schedule Recovery Triggered\n"
                    f"Bot restarted and detected {missed_posts} missed post(s) today.\n"
                    f"Posted so far: {today_posted_startup}/{expected_posts_by_now} expected.\n"
                    f"Catching up now — {missed_posts} pin(s) will post immediately."
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

            # ── Live heartbeat: check if schedule is behind every 30 min ──────
            # Even without a restart, a slot can silently fail (Pinterest API
            # timeout, image error, etc.) and the bot may not retry it.
            # Every 30 minutes we compare posts made vs slots passed. If behind,
            # we immediately trigger a recovery post. If on schedule, skip.
            mins_since_heartbeat = (now - _last_heartbeat_check).total_seconds() / 60
            if mins_since_heartbeat >= 30:
                _last_heartbeat_check = now
                today_posted_hb = count_posts_today(today_ist)
                slots_passed_hb = sum(
                    1 for (h, m) in _get_jittered_times_utc()
                    if (now - now.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds() > 360
                )
                expected_hb = min(slots_passed_hb, MAX_POSTS_PER_DAY)
                missed_hb = max(0, expected_hb - today_posted_hb)
                if missed_hb > 0 and _pending_catchup_count == 0:
                    logger.warning(
                        f"[Scheduler] LIVE RECOVERY: Schedule drifted — "
                        f"{today_posted_hb}/{expected_hb} expected posts made. "
                        f"Triggering {missed_hb} catch-up post(s)."
                    )
                    _pending_catchup_count = missed_hb
                    try:
                        from telegram_bot import notify_admin
                        notify_admin(
                            f"⚡ Live Schedule Recovery\n"
                            f"Detected {missed_hb} missed post(s) mid-day.\n"
                            f"Posted: {today_posted_hb} | Expected by now: {expected_hb}\n"
                            f"Catching up immediately."
                        )
                    except Exception:
                        pass
                elif missed_hb == 0 and slots_passed_hb > 0:
                    logger.info(
                        f"[Scheduler] Heartbeat OK — schedule on track: "
                        f"{today_posted_hb}/{expected_hb} posts. No action needed."
                    )
            # ─────────────────────────────────────────────────────────────────

            # ── Check if it's a posting time slot (with today's jitter applied) ──
            current_slot = None
            jittered_times = _get_jittered_times_utc()
            for (h, m) in jittered_times:
                slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = abs((now - slot_start).total_seconds())
                if diff <= 360:  # within 6 minutes of jittered slot (wider = safer)
                    current_slot = (h, m)
                    break

            # Trigger posting for: (a) normal slot OR (b) missed-slot catch-up
            if (current_slot and current_slot != _last_fired_slot) or _pending_catchup_count > 0:
                if _pending_catchup_count > 0:
                    _pending_catchup_count -= 1  # consume one catch-up credit
                if current_slot:
                    _last_fired_slot = current_slot
                display_slot = current_slot or _last_fired_slot or (0, 0)
                today_posted = count_posts_today(today_ist)

                # Convert UTC slot to IST for display
                slot_ist_total = display_slot[0] * 60 + display_slot[1] + 5 * 60 + 30
                slot_ist_h = (slot_ist_total // 60) % 24
                slot_ist_m = slot_ist_total % 60
                logger.info(
                    f"[Scheduler] Slot fired: {slot_ist_h:02d}:{slot_ist_m:02d} IST (jittered) | "
                    f"Posted today: {today_posted}/{MAX_POSTS_PER_DAY}"
                )

                if today_posted >= MAX_POSTS_PER_DAY:
                    logger.info("[Scheduler] Daily limit reached. Skipping slot.")
                else:
                    # Try DB queue first (priority: new > backlog)
                    pin = get_next_queued_pin(today_ist)

                    if pin:
                        # ── Double duplicate check ─────────────────────────
                        filename = pin["image_path"].split("/")[-1].split("\\")[-1] if pin["image_path"] else ""
                        if (is_file_uploaded(filename) or
                                is_image_url_uploaded(pin.get("image_url", ""))):
                            logger.info(f"[Scheduler] Duplicate detected, removing from queue: {pin['title']}")
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
                                            safe_name = os.path.splitext(os.path.basename(image_path))[0]
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
                                                remove_queued_pin(pin["id"])
                                                logger.error(
                                                    f"[Scheduler] Dropped pin after 3 failed re-downloads: '{pin['title']}'"
                                                )
                                                try:
                                                    from telegram_bot import notify_admin
                                                    notify_admin(
                                                        f"⚠️ Pin dropped — image lost & CDN expired:\n"
                                                        f"'{pin['title']}'\n"
                                                        f"Anime: {pin['anime_name']}\n"
                                                        f"The Telegram CDN URL has expired. "
                                                        f"Re-send the image to re-queue it."
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
                                    increment_retry_count(pin["id"])
                                    image_path = None

                            if image_path:
                                # Build alt_text for Pinterest SEO
                                alt_text = (
                                    f"{pin['anime_name']} anime art poster wallpaper "
                                    f"{pin['title'].replace('-', ' ')}"
                                )[:500]

                                success = upload_to_pinterest(
                                    image_path=image_path,
                                    title=pin["title"],
                                    description=pin["description"],
                                    link=pin["link"],
                                    anime_name=pin["anime_name"],
                                    board_id=pin.get("board_id", ""),
                                    alt_text=alt_text,
                                )
                                if success:
                                    remove_queued_pin(pin["id"])
                                    now_ist = _ist_now().strftime("%I:%M %p")
                                    pin_type = "NEW" if pin["priority"] == 1 else "BACKLOG"
                                    counts_after = count_posts_today(today_ist)
                                    logger.info(
                                        f"[Scheduler] Pin posted. Today: "
                                        f"{counts_after}/{MAX_POSTS_PER_DAY}"
                                    )
                                    self._notify_pin_posted(
                                        title=pin["title"],
                                        anime_name=pin["anime_name"],
                                        link=pin["link"],
                                        image_path=image_path,
                                        pin_type=pin_type,
                                        posted_today=counts_after,
                                        time_ist=now_ist,
                                    )
                                else:
                                    # Auto-retry: drop after 3 fails
                                    MAX_RETRIES = 3
                                    new_count = increment_retry_count(pin["id"])
                                    if new_count >= MAX_RETRIES:
                                        logger.error(
                                            f"[Scheduler] Pin failed {MAX_RETRIES} times, "
                                            f"dropping: '{pin['title']}'"
                                        )
                                        remove_queued_pin(pin["id"])
                                        try:
                                            from telegram_bot import notify_admin
                                            notify_admin(
                                                f"[Bot Alert] Pin dropped after {MAX_RETRIES} failed "
                                                f"upload attempts:\n'{pin['title']}'\n"
                                                f"Anime: {pin['anime_name']}\n"
                                                f"Check /logs for details."
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
        if not self.is_running:
            self.thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.thread.start()
            mins = self._minutes_to_next_slot()
            logger.info(f"[Scheduler] Next posting slot in ~{mins} minutes.")

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)


# Global instance
scheduler = PinScheduler()
