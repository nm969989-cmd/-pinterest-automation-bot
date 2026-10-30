import datetime
from unittest.mock import patch, MagicMock, AsyncMock

import scheduler
import telegram_listener
import database


def test_get_slot_diagnostics_structure():
    """Verify get_slot_diagnostics returns all expected keys and structured slot data."""
    fake_now = datetime.datetime(2026, 10, 31, 10, 0, 0)
    with patch.object(scheduler, "_ist_now", return_value=fake_now), \
         patch.object(scheduler, "_today_ist", return_value="2026-10-31"), \
         patch.object(database, "count_posts_today", return_value=1), \
         patch.object(database, "get_queue_counts", return_value={"total": 4, "new": 2, "backlog": 2}):
        
        diag = scheduler.get_slot_diagnostics()
        assert diag["date_ist"] == "31 Oct 2026"
        assert diag["today_str"] == "2026-10-31"
        assert diag["posted_today"] == 1
        assert diag["max_posts"] == 5
        assert diag["remaining_today"] == 4
        assert "slots" in diag
        assert len(diag["slots"]) == 5
        assert diag["queue"]["total"] == 4
        assert diag["next_slot"] is not None


def test_get_slot_diagnostics_shows_skipped_when_quota_exhausted():
    """When daily limit is reached early, past un-posted slots and future slots show clear limit indicators."""
    fake_now = datetime.datetime(2026, 10, 31, 15, 0, 0)
    with patch.object(scheduler, "_ist_now", return_value=fake_now), \
         patch.object(scheduler, "_today_ist", return_value="2026-10-31"), \
         patch.object(database, "count_posts_today", return_value=5), \
         patch.object(database, "get_queue_counts", return_value={"total": 2, "new": 2, "backlog": 0}):
        
        diag = scheduler.get_slot_diagnostics()
        assert diag["posted_today"] == 5
        assert diag["remaining_today"] == 0
        # Future slots should indicate they will skip
        future_slots = [s for s in diag["slots"] if not s["is_past"]]
        for s in future_slots:
            assert "Daily quota reached" in s["status"]


def test_ensure_queue_stocked_triggers_when_low():
    """ensure_queue_stocked triggers scrape_all_channels when total count is less than target."""
    counts = [{"total": 2, "backlog": 0, "new": 2}, {"total": 5, "backlog": 3, "new": 2}]
    
    with patch.object(database, "get_queue_counts", side_effect=counts), \
         patch.object(telegram_listener, "scrape_all_channels") as mock_scrape:
        
        final_total = telegram_listener.ensure_queue_stocked(target_count=5)
        mock_scrape.assert_called_once_with(max_backlog=5)
        assert final_total == 5


def test_ensure_queue_stocked_noop_when_full():
    """ensure_queue_stocked does not trigger scrape when total count meets target."""
    with patch.object(database, "get_queue_counts", return_value={"total": 6, "backlog": 4, "new": 2}), \
         patch.object(telegram_listener, "scrape_all_channels") as mock_scrape:
        
        final_total = telegram_listener.ensure_queue_stocked(target_count=5)
        mock_scrape.assert_not_called()
        assert final_total == 6


import pytest

@pytest.mark.asyncio
async def test_cmd_slotstatus_replies_to_admin():
    """Verify /slotstatus formats correctly and sends message to admin."""
    import telegram_bot

    update = MagicMock()
    context = MagicMock()
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    update.effective_message = msg

    with patch.object(telegram_bot, "_is_admin", return_value=True), \
         patch.object(scheduler, "get_slot_diagnostics", return_value={
             "date_ist": "31 Oct 2026",
             "today_str": "2026-10-31",
             "posted_today": 0,
             "max_posts": 5,
             "remaining_today": 5,
             "next_slot": {"slot_num": 1, "time_str": "09:00 AM IST", "countdown": "in ~1h"},
             "slots": [
                 {"slot_num": 1, "time_ist": "09:00 AM IST", "is_past": False, "status": "⏳ in ~1h"},
                 {"slot_num": 2, "time_ist": "01:00 PM IST", "is_past": False, "status": "⏳ in ~5h"},
                 {"slot_num": 3, "time_ist": "04:00 PM IST", "is_past": False, "status": "⏳ in ~8h"},
                 {"slot_num": 4, "time_ist": "06:00 PM IST", "is_past": False, "status": "⏳ in ~10h"},
                 {"slot_num": 5, "time_ist": "08:00 PM IST", "is_past": False, "status": "⏳ in ~12h"},
             ],
             "queue": {"total": 5, "new": 2, "backlog": 3},
         }):
        
        await telegram_bot.cmd_slotstatus(update, context)
        msg.reply_text.assert_called_once()
        text_arg = msg.reply_text.call_args[0][0]
        assert "📊 *Slot & Daily Posting Status*" in text_arg
        assert "0 / 5" in text_arg
        assert "09:00 AM IST" in text_arg
        assert "Syndication Platforms" in text_arg

