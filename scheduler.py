"""
Smart Pin Scheduler
===================
- Posts 3 pins/day at human-like randomized IST times around 9 AM, 1 PM, 6 PM
- Each slot has a daily random jitter of +/-20 minutes (looks 100% human to Pinterest)
- Jitter is regenerated each day at midnight so times vary every day
- Priority: NEW images always post before BACKLOG images
- If 10 new images arrive -> 3 today, 7 scheduled across next days
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

# Base IST posting times: 9:00 AM, 1:00 PM, 6:00 PM
# IST = UTC+5:30, so in UTC: 3:30, 7:30, 12:30
_BASE_POST_TIMES_UTC = [
    (3, 30),   # 09:00 AM IST
    (7, 30),   # 01:00 PM IST
    (12, 30),  # 06:00 PM IST
]

# Anti-bot jitter: max +/- minutes to randomize each slot
# Pinterest spam detection flags accounts that post at exact clockwork times.
_JITTER_MAX_MINUTES = 20

# ── Daily jitter state ────────────────────────────────────────────────────────
# Generated once per day. Stores (day_str, [(h_offset, m_offset), ...])
_jitter_cache: tuple[str, list] = ("", [])


def _get_daily_jitter() -> list[tuple[int, int]]:
    """
    Returns the list of (hour_offset, minute_offset) for each slot today.
    Re-generates fresh random offsets every new calendar day (IST).
    Offsets are in range [-JITTER_MAX_MINUTES, +JITTER_MAX_MINUTES] minutes.
    """
    global _jitter_cache
    today = _ist_now().strftime("%Y-%m-%d")
    if _jitter_cache[0] != today:
        offsets = []
        for _ in _BASE_POST_TIMES_UTC:
            jitter_minutes = random.randint(-_JITTER_MAX_MINUTES, _JITTER_MAX_MINUTES)
            offsets.append(jitter_minutes)
        _jitter_cache = (today, offsets)
        logger.info(
            f"[Scheduler] New daily jitter generated for {today}: "
            + ", ".join(f"{'+' if j >= 0 else ''}{j}min" for j in offsets)
        )
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
        Slot window: fires within a ±2 minute window of the target time.
        """
        now = datetime.datetime.utcnow()
        for (h, m) in _get_jittered_times_utc():
            slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
            diff = abs((now - slot_start).total_seconds())
            if diff <= 120:  # within 2 minutes of slot
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
            f"(base: 09:00, 13:00, 18:00 +/- up to {_JITTER_MAX_MINUTES}min)"
        )
        _last_fired_slot = None

        while self.is_running:
            now = datetime.datetime.utcnow()
            today_ist = _today_ist()

            # ── Check if it's a posting time slot (with today's jitter applied) ──
            current_slot = None
            jittered_times = _get_jittered_times_utc()
            for (h, m) in jittered_times:
                slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = abs((now - slot_start).total_seconds())
                if diff <= 180:  # within 3 minutes of jittered slot
                    current_slot = (h, m)
                    break

            if current_slot and current_slot != _last_fired_slot:
                _last_fired_slot = current_slot
                today_posted = count_posts_today(today_ist)

                # Convert UTC slot to IST for display
                slot_ist_total = current_slot[0] * 60 + current_slot[1] + 5 * 60 + 30
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
                            # the queue row has the original Telegram CDN URL,
                            # re-download and re-process it automatically.
                            image_path = pin["image_path"]
                            if not os.path.exists(image_path):
                                cdn_url = pin.get("image_url", "")
                                if cdn_url and cdn_url.startswith("http"):
                                    logger.warning(
                                        f"[Scheduler] Image file missing (Render FS wipe?): {image_path}\n"
                                        f"[Scheduler] Re-downloading from CDN: {cdn_url}"
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
                    abs((now - now.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds()) <= 180
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
