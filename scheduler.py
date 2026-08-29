"""
Smart Pin Scheduler
===================
- Posts exactly 3 pins/day at fixed IST times: 9:00 AM, 1:00 PM, 6:00 PM
- Priority: NEW images always post before BACKLOG images
- If 10 new images arrive → 3 today, 7 scheduled across next days
- If new images arrive while backlog is pending → NEW images go first
- Multi-day queue is persisted in SQLite (survives restarts)
"""

import time
import threading
import datetime
from collections import deque
from logger import get_logger
from config import MAX_POSTS_PER_DAY

logger = get_logger(__name__)

# Fixed IST posting times: 9:00 AM, 1:00 PM, 6:00 PM
# IST = UTC+5:30, so in UTC: 3:30, 7:30, 12:30
_POST_TIMES_UTC = [
    (3, 30),   # 09:00 AM IST
    (7, 30),   # 01:00 PM IST
    (12, 30),  # 06:00 PM IST
]


def _ist_now() -> datetime.datetime:
    """Current time in IST (UTC+5:30)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def _today_ist() -> str:
    return _ist_now().strftime("%Y-%m-%d")


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
                          image_url: str = ""):
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
                image_url=image_url, priority=1, scheduled_date=sched_date
            )
            if added:
                logger.info(
                    f"[Scheduler] NEW pin queued for {sched_date}: '{title}' "
                    f"(queue: {counts['new']+1} new, {counts['backlog']} backlog)"
                )
            return added

    def enqueue_backlog_image(self, post_id: str, image_path: str, title: str,
                               description: str, link: str, anime_name: str,
                               image_url: str = ""):
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
                image_url=image_url, priority=0, scheduled_date=sched_date
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
        for (h, m) in _POST_TIMES_UTC:
            slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
            diff = abs((now - slot_start).total_seconds())
            if diff <= 120:  # within 2 minutes of slot
                return True
        return False

    def _minutes_to_next_slot(self) -> int:
        """Returns minutes until the next posting slot."""
        now = datetime.datetime.utcnow()
        today = now.date()
        candidates = []
        for (h, m) in _POST_TIMES_UTC:
            slot = datetime.datetime(today.year, today.month, today.day, h, m)
            if slot > now:
                candidates.append(slot)
        # Also check tomorrow's first slot
        tomorrow = today + datetime.timedelta(days=1)
        h0, m0 = _POST_TIMES_UTC[0]
        candidates.append(datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, h0, m0))

        next_slot = min(candidates)
        return max(1, int((next_slot - now).total_seconds() / 60))

    def _worker_loop(self):
        from database import (get_next_queued_pin, remove_queued_pin,
                               count_posts_today, mark_file_uploaded,
                               is_file_uploaded, is_image_url_uploaded)
        from pinterest_uploader import upload_to_pinterest

        self.is_running = True
        logger.info(
            f"[Scheduler] Started. Max {MAX_POSTS_PER_DAY} pins/day at "
            f"09:00, 13:00, 18:00 IST. New images have priority over backlog."
        )
        _last_fired_slot = None

        while self.is_running:
            now = datetime.datetime.utcnow()
            today_ist = _today_ist()

            # ── Check if it's a posting time slot ──────────────────────────
            current_slot = None
            for (h, m) in _POST_TIMES_UTC:
                slot_start = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = abs((now - slot_start).total_seconds())
                if diff <= 120:
                    current_slot = (h, m)
                    break

            if current_slot and current_slot != _last_fired_slot:
                _last_fired_slot = current_slot
                today_posted = count_posts_today(today_ist)

                slot_ist_h = (current_slot[0] + 5) % 24
                slot_ist_m = (current_slot[1] + 30) % 60
                logger.info(
                    f"[Scheduler] Slot fired: {slot_ist_h:02d}:{slot_ist_m:02d} IST | "
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
                            success = upload_to_pinterest(
                                image_path=pin["image_path"],
                                title=pin["title"],
                                description=pin["description"],
                                link=pin["link"],
                                anime_name=pin["anime_name"],
                            )
                            if success:
                                remove_queued_pin(pin["id"])
                                logger.info(
                                    f"[Scheduler] ✅ Pin posted. Today: "
                                    f"{today_posted+1}/{MAX_POSTS_PER_DAY}"
                                )
                            else:
                                logger.error(
                                    "[Scheduler] ❌ Upload failed. Will retry next slot."
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
                # Reset slot tracker when outside all slot windows
                all_outside = not any(
                    abs((now - now.replace(hour=h, minute=m, second=0, microsecond=0)).total_seconds()) <= 120
                    for (h, m) in _POST_TIMES_UTC
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
