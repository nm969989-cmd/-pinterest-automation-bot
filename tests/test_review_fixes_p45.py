"""Regression tests for code-review fixes, phases 4 and 5."""
import asyncio
import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import database


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    """Isolated SQLite DB (same approach as the existing adversarial tests)."""
    def _reset():
        conn = getattr(database._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        database._local.conn = None

    _reset()
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()
    yield tmp_path
    _reset()


def _ist_today():
    with database._get_conn() as conn:
        return conn.execute("SELECT date('now','+5 hours','+30 minutes')").fetchone()[0]


# -- Phase 4 ------------------------------------------------------------------
def test_one_post_with_two_filename_rows_counts_once(tmpdb):
    database.mark_file_uploaded("processed_name.jpg", title="Same Post")
    database.mark_file_uploaded("original_name.png", title="Same Post")  # alias row
    assert database.count_posts_today(_ist_today()) == 1


def test_distinct_posts_still_counted_separately(tmpdb):
    database.mark_file_uploaded("a.jpg", title="Post A")
    database.mark_file_uploaded("b.jpg", title="Post B")
    assert database.count_posts_today(_ist_today()) == 2


def test_cleanup_keeps_files_referenced_by_queued_pins(tmpdb, monkeypatch):
    import crash_protection
    monkeypatch.chdir(tmpdb)
    os.makedirs("processed")
    old = time.time() - 48 * 3600
    for name in ("queued.jpg", "stale.jpg"):
        p = os.path.join("processed", name)
        open(p, "wb").write(b"x")
        os.utime(p, (old, old))
    with database._get_conn() as conn:
        conn.execute(
            "INSERT INTO pin_queue (post_id, image_path, title, description, link, anime_name, priority, scheduled_date) "
            "VALUES ('p1', 'processed/queued.jpg', 't', 'd', 'l', 'a', 1, '2999-01-01')"
        )
        conn.commit()
    removed = crash_protection.cleanup_old_files(max_age_hours=1)
    assert removed == 1
    assert os.path.exists(os.path.join("processed", "queued.jpg"))
    assert not os.path.exists(os.path.join("processed", "stale.jpg"))


def test_emergency_restart_exits_nonzero():
    import inspect
    import crash_protection
    assert "os._exit(75)" in inspect.getsource(crash_protection._emergency_restart)


def test_slots_fired_state_survives_loop_restart():
    import scheduler as sched
    s = sched.PinScheduler()
    s._slots_fired_store.add((9, 0, "2030-01-01"))
    assert (9, 0, "2030-01-01") in s._slots_fired_store


# -- Phase 5: analytics -------------------------------------------------------
def test_pinterest_in_app_browser_is_not_a_bot():
    from pinterest_analytics import is_bot_user_agent
    in_app = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 [Pinterest/iOS]"
    assert is_bot_user_agent(in_app) is False


def test_pinterest_crawler_is_still_a_bot():
    from pinterest_analytics import is_bot_user_agent
    assert is_bot_user_agent("Pinterest/0.2 (+http://www.pinterest.com/bot.html)") is True


def test_top_pins_does_not_inflate_visits_with_clicks(tmpdb):
    import pinterest_analytics as pa
    pa._analytics_table_ensured = False
    for _ in range(2):
        pa.record_bridge_visit("abc123", "Naruto", "Title", user_agent="Mozilla/5.0 Chrome")
    with database._get_conn() as conn:
        for _ in range(3):
            conn.execute(
                "INSERT INTO link_clicks (code, anime_name, title, merchant, country, image_filename) "
                "VALUES ('abc123', 'Naruto', 'Title', 'amazon', 'IN', 'x.jpg')"
            )
        conn.commit()
    top = pa.get_top_pins(days=7)
    assert len(top) == 1
    assert top[0]["visits"] == 2      # was 6 (2 visits x 3 clicks) before the fix
    assert top[0]["clicks"] == 3


# -- Phase 5: JSONBin ---------------------------------------------------------
def test_payload_includes_imgbb_imagekit_imgpile(tmpdb):
    import jsonbin_sync
    with database._get_conn() as conn:
        conn.execute("INSERT INTO imgbb_posts (filename, post_url, title, image_url) VALUES ('a.jpg','u','t','i')")
        conn.execute("INSERT INTO imagekit_posts (filename, post_url, title, image_url, file_id) VALUES ('b.jpg','u','t','i','f1')")
        conn.execute("INSERT INTO imgpile_posts (filename, post_url, title, image_url) VALUES ('c.jpg','u','t','i')")
        conn.commit()
    payload, _ = jsonbin_sync.build_state_payload()
    assert [r["filename"] for r in payload["imgbb_posts"]] == ["a.jpg"]
    assert payload["imagekit_posts"][0]["file_id"] == "f1"
    assert [r["filename"] for r in payload["imgpile_posts"]] == ["c.jpg"]


def test_restore_brings_back_imgbb_imagekit_imgpile_dedupe(tmpdb, monkeypatch):
    import jsonbin_sync
    state = {
        "imgbb_posts": [{"filename": "a.jpg", "post_url": "u", "title": "t", "image_url": "i"}],
        "imagekit_posts": [{"filename": "b.jpg", "post_url": "u", "title": "t", "image_url": "i", "file_id": "f1"}],
        "imgpile_posts": [{"filename": "c.jpg", "post_url": "u", "title": "t", "image_url": "i"}],
    }
    monkeypatch.setattr(jsonbin_sync, "_is_configured", lambda: True)
    monkeypatch.setattr(jsonbin_sync, "load_cloud_state", lambda: state)
    jsonbin_sync.restore_db_from_cloud()
    assert database.is_imgbb_posted("a.jpg")
    assert database.is_imagekit_posted("b.jpg")
    assert database.is_imgpile_posted("c.jpg")


def test_failed_boot_restore_blocks_cloud_overwrite(tmpdb, monkeypatch):
    import jsonbin_sync
    monkeypatch.setattr(jsonbin_sync, "_cloud_restore_failed", False)
    monkeypatch.setattr(jsonbin_sync, "_is_configured", lambda: True)
    monkeypatch.setattr(jsonbin_sync, "load_cloud_state", lambda: None)  # timeout / 5xx
    jsonbin_sync.restore_db_from_cloud()
    assert jsonbin_sync._cloud_restore_failed is True

    put = MagicMock()
    monkeypatch.setattr(jsonbin_sync, "_retry_restore_if_needed", lambda *a, **k: None)
    with patch.object(jsonbin_sync.requests, "put", put):
        assert jsonbin_sync.save_cloud_state() is False
    put.assert_not_called()


def test_empty_cloud_bin_is_not_treated_as_failure(tmpdb, monkeypatch):
    import jsonbin_sync
    monkeypatch.setattr(jsonbin_sync, "_cloud_restore_failed", True)
    monkeypatch.setattr(jsonbin_sync, "_is_configured", lambda: True)
    monkeypatch.setattr(jsonbin_sync, "load_cloud_state", lambda: {})
    jsonbin_sync.restore_db_from_cloud()
    assert jsonbin_sync._cloud_restore_failed is False


def test_successful_restore_reenables_saves(tmpdb, monkeypatch):
    import jsonbin_sync
    monkeypatch.setattr(jsonbin_sync, "_cloud_restore_failed", True)
    monkeypatch.setattr(jsonbin_sync, "_is_configured", lambda: True)
    monkeypatch.setattr(jsonbin_sync, "load_cloud_state", lambda: {"processed_posts": ["p1"]})
    jsonbin_sync.restore_db_from_cloud()
    assert jsonbin_sync._cloud_restore_failed is False


def test_secret_metadata_excluded_only_when_opted_in(tmpdb, monkeypatch):
    import jsonbin_sync
    database.set_metadata("deviantart_refresh_token", "secret-value")
    database.set_metadata("harmless_key", "ok")

    monkeypatch.delenv("JSONBIN_EXCLUDE_SECRETS", raising=False)
    keys = {m["key"] for m in jsonbin_sync.build_state_payload()[0]["bot_metadata"]}
    assert "deviantart_refresh_token" in keys  # default: rotating OAuth tokens must survive restarts

    monkeypatch.setenv("JSONBIN_EXCLUDE_SECRETS", "true")
    keys = {m["key"] for m in jsonbin_sync.build_state_payload()[0]["bot_metadata"]}
    assert "deviantart_refresh_token" not in keys
    assert "harmless_key" in keys


# -- Phase 5: dispatcher ------------------------------------------------------
def test_port_number_103_does_not_trip_breaker():
    import crosspost_dispatcher as cd
    with patch.object(cd, "trip_breaker") as trip:
        cd._handle_platform_error("bluesky", "HTTPSConnectionPool(host='x', port=10345): timed out")
        trip.assert_not_called()


def test_real_403_still_trips_breaker():
    import crosspost_dispatcher as cd
    with patch.object(cd, "trip_breaker") as trip:
        cd._handle_platform_error("bluesky", "HTTP 403 Forbidden")
        trip.assert_called_once()


# -- Phase 5: delivery confirmation ------------------------------------------
@pytest.fixture
def tg_loop(monkeypatch):
    import telegram_bot
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    monkeypatch.setattr(telegram_bot, "_loop_ref", loop)
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "42")
    monkeypatch.setattr(telegram_bot, "_anti_flood_timestamps", [])
    yield telegram_bot
    loop.call_soon_threadsafe(loop.stop)


def test_confirmed_notify_falls_back_to_plain_text(tg_loop, monkeypatch):
    calls = []

    class Bot:
        async def send_message(self, **kw):
            calls.append(kw.get("parse_mode"))
            if kw.get("parse_mode"):
                raise RuntimeError("can't parse entities")

    monkeypatch.setattr(tg_loop, "_app_ref", MagicMock(bot=Bot()))
    assert tg_loop.notify_admin_confirmed("Naruto_Uzumaki *bad", parse_mode="Markdown") is True
    assert calls == ["Markdown", None]


def test_confirmed_notify_returns_false_when_undeliverable(tg_loop, monkeypatch):
    class Bot:
        async def send_message(self, **kw):
            raise RuntimeError("network down")

    monkeypatch.setattr(tg_loop, "_app_ref", MagicMock(bot=Bot()))
    assert tg_loop.notify_admin_confirmed("hi") is False


def test_digest_not_marked_sent_when_undelivered(tmpdb, monkeypatch):
    import daily_digest
    import telegram_bot
    monkeypatch.setattr(daily_digest, "_last_failed_attempt_ts", 0.0)
    monkeypatch.setattr(daily_digest, "build_daily_digest_message", lambda date_str=None: "report")
    monkeypatch.setattr(telegram_bot, "notify_admin_confirmed", lambda *a, **k: False)
    assert daily_digest.send_daily_digest(force=False) is False
    assert database.get_metadata("last_daily_digest_date", "") == ""


def test_digest_marked_sent_when_delivered(tmpdb, monkeypatch):
    import daily_digest
    import telegram_bot
    monkeypatch.setattr(daily_digest, "_last_failed_attempt_ts", 0.0)
    monkeypatch.setattr(daily_digest, "build_daily_digest_message", lambda date_str=None: "report")
    monkeypatch.setattr(telegram_bot, "notify_admin_confirmed", lambda *a, **k: True)
    assert daily_digest.send_daily_digest(force=False) is True
    assert database.get_metadata("last_daily_digest_date", "") != ""


def test_weekly_not_marked_sent_when_undelivered(tmpdb, monkeypatch):
    import weekly_report
    import telegram_bot
    monkeypatch.setattr(weekly_report, "_last_failed_attempt_ts", 0.0)
    monkeypatch.setattr(weekly_report, "_last_sent_week_key", "")
    monkeypatch.setattr(weekly_report, "build_weekly_report", lambda: "report")
    monkeypatch.setattr(telegram_bot, "notify_admin_confirmed", lambda *a, **k: False)
    assert weekly_report.send_weekly_report(force=False) is False
    assert weekly_report._last_sent_week_key == ""
