import pytest
import datetime
from unittest.mock import patch, MagicMock

import config
from weekly_report import (
    build_weekly_report,
    get_amazon_report_markup,
    check_and_send_weekly_report,
    _ist_now,
)


def test_build_weekly_report_format():
    report = build_weekly_report()
    assert isinstance(report, str)
    assert "Weekly Performance Report" in report
    assert "Real Traffic & Click Attribution" in report
    assert "Amazon Associates Status" in report
    assert "https://affiliate-program.amazon.com/home/reports" in report
    
    # Ensure no arbitrary fake math string like ₹25/click
    assert "₹25.0/click" not in report
    assert "₹25 per click" not in report


def test_get_amazon_report_markup():
    markup = get_amazon_report_markup()
    assert markup is not None
    # Check button text and url
    button = markup.inline_keyboard[0][0]
    assert "Amazon Associates" in button.text
    assert "affiliate-program.amazon.com" in button.url


def test_weekly_report_timing_trigger():
    # Simulate Sunday at 09:05 AM IST (should trigger)
    sunday_9am = datetime.datetime(2026, 11, 1, 9, 5, 0) # Nov 1, 2026 is Sunday (weekday=6)
    assert sunday_9am.weekday() == 6

    with patch("weekly_report._ist_now", return_value=sunday_9am), \
         patch("weekly_report.send_weekly_report", return_value=True) as mock_send, \
         patch("weekly_report._last_sent_week_key", ""):
        triggered = check_and_send_weekly_report()
        assert triggered is True
        mock_send.assert_called_once_with(force=False)

    # Simulate Saturday (weekday=5, should NOT trigger)
    saturday_9am = datetime.datetime(2026, 10, 31, 9, 5, 0)
    with patch("weekly_report._ist_now", return_value=saturday_9am), \
         patch("weekly_report.send_weekly_report", return_value=True) as mock_send, \
         patch("weekly_report._last_sent_week_key", ""):
        triggered = check_and_send_weekly_report()
        assert triggered is False
        mock_send.assert_not_called()
