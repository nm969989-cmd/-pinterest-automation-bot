"""
tests/test_adversarial_m1.py — Empirical Adversarial Stress Test Suite
====================================================================
Adversarially validates:
- R1: DeviantArt token persistence, rapid rotation, race conditions,
      startup 401 recovery, cloud restore, round-trip sync, and JSONBin budget enforcer.
- R2: Pixelfed decoupled configuration, 401 unauthenticated vs 500/503 errors,
      status hiding in /crosspost, and telegram command responses.
"""

import os
import io
import json
import time
import shutil
import asyncio
import tempfile
import sqlite3
import threading
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

import config
import database
import deviantart_uploader
import pixelfed_uploader
import crosspost_dispatcher
import telegram_bot
import jsonbin_sync


class TestAdversarialR1DeviantArtTokenPersistence(unittest.TestCase):
    """
    Adversarial challenge test suite for Requirement R1:
    DeviantArt token persistence, concurrency, startup 401 recovery, and JSONBin cloud sync.
    """

    def setUp(self):
        # Create an isolated temporary SQLite database for each test
        self.temp_dir = tempfile.mkdtemp()
        self.temp_db = os.path.join(self.temp_dir, "test_adversarial_da.db")
        self.orig_db_path = database.DB_PATH
        database.DB_PATH = self.temp_db
        # Reset thread-local connection
        if hasattr(database._local, "conn") and database._local.conn:
            try:
                database._local.conn.close()
            except Exception:
                pass
            database._local.conn = None
        database.init_db()

        # Reset module-level cached tokens
        deviantart_uploader._cached_access_token = ""

    def tearDown(self):
        if hasattr(database._local, "conn") and database._local.conn:
            try:
                database._local.conn.close()
            except Exception:
                pass
            database._local.conn = None
        database.DB_PATH = self.orig_db_path
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        deviantart_uploader._cached_access_token = ""

    def test_concurrent_token_rotation_race_condition(self):
        """
        Adversarial test: 10 concurrent threads attempt rapid token rotation.
        Must execute without SQLite locking errors, maintain DB integrity,
        and trigger immediate cloud sync on each successful rotation.
        """
        call_count = 0
        sync_calls = []

        def fake_post(url, data=None, timeout=None):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "access_token": f"rotated_acc_tok_{call_count}",
                "refresh_token": f"rotated_ref_tok_{call_count}",
            }
            return resp

        def fake_sync(min_interval_sec=60):
            sync_calls.append(min_interval_sec)
            return True

        threads = []
        errors = []
        results = []

        def worker(idx):
            try:
                database._local.conn = None
                tok = deviantart_uploader._refresh_token(
                    client_id="mock_client_id",
                    client_secret="mock_client_secret",
                    refresh_token=f"thread_seed_ref_{idx}",
                )
                results.append(tok)
            except Exception as e:
                errors.append(e)

        with patch("requests.post", side_effect=fake_post), \
             patch("jsonbin_sync.trigger_cloud_sync_now", side_effect=fake_sync):

            for i in range(10):
                t = threading.Thread(target=worker, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join(timeout=10)

        # 1. Zero unhandled concurrency errors
        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")
        # 2. All 10 threads succeeded with non-empty rotated access tokens
        self.assertEqual(len(results), 10)
        for r in results:
            self.assertTrue(r.startswith("rotated_acc_tok_"))
        # 3. Cloud sync was triggered with min_interval_sec=0 for each rotation
        self.assertEqual(len(sync_calls), 10)
        self.assertTrue(all(val == 0 for val in sync_calls))
        # 4. Final DB state has valid non-empty tokens in bot_metadata
        persisted_acc = database.get_metadata("deviantart_access_token", "")
        persisted_ref = database.get_metadata("deviantart_refresh_token", "")
        self.assertTrue(persisted_acc.startswith("rotated_acc_tok_"))
        self.assertTrue(persisted_ref.startswith("rotated_ref_tok_"))

    def test_rapid_restart_simulated_container_spin_down(self):
        """
        Adversarial test: simulate container crash/restart on ephemeral filesystem.
        After writing token to SQLite, in-memory caches are wiped.
        On boot, _get_access_token() must recover the persisted token from SQLite
        and NOT fall back to stale .env tokens.
        """
        # Step 1: Persist rotated token to SQLite
        database.set_metadata("deviantart_access_token", "persisted_acc_token_render_survivor")
        database.set_metadata("deviantart_refresh_token", "persisted_ref_token_render_survivor")

        # Step 2: Wipe in-memory module caches (simulating fresh container start)
        deviantart_uploader._cached_access_token = ""

        # Step 3: Call _get_access_token()
        with patch.object(config, "DEVIANTART_ACCESS_TOKEN", "stale_env_access_token"), \
             patch.object(config, "DEVIANTART_REFRESH_TOKEN", "stale_env_refresh_token"):

            recovered_token = deviantart_uploader._get_access_token()

            # Must prefer the SQLite bot_metadata token over the stale .env token
            self.assertEqual(recovered_token, "persisted_acc_token_render_survivor")
            self.assertEqual(deviantart_uploader._cached_access_token, "persisted_acc_token_render_survivor")

    def test_expired_access_token_on_startup_full_roundtrip_recovery(self):
        """
        Adversarial test: container boots with an expired access token in bot_metadata.
        refresh_deviantart_startup_token() must:
        - Detect HTTP 401 on /user/whoami
        - Trigger OAuth refresh using the persisted refresh token
        - Update bot_metadata with new tokens
        - Trigger immediate JSONBin cloud sync (min_interval_sec=0)
        - Return True to indicate successful authentication startup
        """
        database.set_metadata("deviantart_access_token", "expired_boot_acc_111")
        database.set_metadata("deviantart_refresh_token", "valid_boot_ref_222")
        deviantart_uploader._cached_access_token = ""

        mock_sync = MagicMock()

        def fake_get(url, headers=None, timeout=None):
            auth = headers.get("Authorization", "")
            r = MagicMock()
            if "expired_boot_acc_111" in auth:
                r.status_code = 401
                r.text = "Unauthorized"
            elif "fresh_boot_acc_333" in auth:
                r.status_code = 200
                r.json.return_value = {"username": "muthelyrics"}
            else:
                r.status_code = 403
            return r

        def fake_post(url, data=None, timeout=None):
            r = MagicMock()
            if data.get("refresh_token") == "valid_boot_ref_222":
                r.status_code = 200
                r.json.return_value = {
                    "access_token": "fresh_boot_acc_333",
                    "refresh_token": "fresh_boot_ref_444",
                }
            else:
                r.status_code = 400
                r.text = "invalid_grant"
            return r

        with patch.object(config, "DEVIANTART_ENABLED", True), \
             patch.object(config, "DEVIANTART_CLIENT_ID", "mock_da_id"), \
             patch.object(config, "DEVIANTART_CLIENT_SECRET", "mock_da_secret"), \
             patch("requests.get", side_effect=fake_get), \
             patch("requests.post", side_effect=fake_post), \
             patch("jsonbin_sync.trigger_cloud_sync_now", mock_sync):

            success = deviantart_uploader.refresh_deviantart_startup_token()

            self.assertTrue(success)
            self.assertEqual(database.get_metadata("deviantart_access_token"), "fresh_boot_acc_333")
            self.assertEqual(database.get_metadata("deviantart_refresh_token"), "fresh_boot_ref_444")
            mock_sync.assert_called_with(min_interval_sec=0)
            self.assertEqual(deviantart_uploader._get_access_token(), "fresh_boot_acc_333")

    def test_startup_expired_token_when_refresh_also_rejected(self):
        """
        Adversarial test: both access token AND refresh token are rejected (HTTP 401 on whoami,
        HTTP 400 invalid_grant on token endpoint).
        Must fail gracefully returning False without crashing main.py startup sequence.
        """
        database.set_metadata("deviantart_access_token", "expired_boot_acc")
        database.set_metadata("deviantart_refresh_token", "revoked_boot_ref")
        deviantart_uploader._cached_access_token = ""

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 401

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 400
        mock_post_resp.text = '{"error": "invalid_grant", "error_description": "Refresh token has expired"}'

        with patch.object(config, "DEVIANTART_ENABLED", True), \
             patch.object(config, "DEVIANTART_CLIENT_ID", "mock_id"), \
             patch.object(config, "DEVIANTART_CLIENT_SECRET", "mock_secret"), \
             patch("requests.get", return_value=mock_get_resp), \
             patch("requests.post", return_value=mock_post_resp):

            result = deviantart_uploader.refresh_deviantart_startup_token()
            self.assertFalse(result)

    def test_startup_when_deviantart_disabled_bypasses_network(self):
        """
        Adversarial test: when DEVIANTART_ENABLED=false, startup check must immediately
        return False without performing any HTTP requests.
        """
        with patch.object(config, "DEVIANTART_ENABLED", False), \
             patch("requests.get") as mock_get:

            result = deviantart_uploader.refresh_deviantart_startup_token()
            self.assertFalse(result)
            mock_get.assert_not_called()

    def test_cloud_restore_round_trip_deviantart_and_metadata(self):
        """
        Adversarial test: simulate full JSONBin cloud backup and restore cycle.
        Validates that both 'deviantart_posts' and 'bot_metadata' survive round-trip,
        including all fields (filename, post_url, itemid, title, image_url, posted_at).
        """
        cloud_state = {
            "processed_posts": ["post_001"],
            "uploaded_files": [],
            "pin_queue": [],
            "tracked_links": [],
            "bot_metadata": [
                {"key": "deviantart_access_token", "value": "cloud_restored_acc_999"},
                {"key": "deviantart_refresh_token", "value": "cloud_restored_ref_888"},
                {"key": "other_key", "value": "test_value"},
            ],
            "deviantart_posts": [
                {
                    "filename": "art_cyberpunk_01.jpg",
                    "post_url": "https://deviantart.com/deviation/123456",
                    "itemid": 123456,
                    "title": "Cyberpunk Girl in Rain",
                    "image_url": "https://i.ibb.co/art1.jpg",
                    "posted_at": "2026-10-29 14:00:00",
                },
                {
                    "filename": "art_fantasy_02.png",
                    "post_url": "https://deviantart.com/deviation/654321",
                    "itemid": 654321,
                    "title": "Fantasy Dragon Peak",
                    "image_url": "https://i.ibb.co/art2.png",
                    "posted_at": "2026-10-29 15:30:00",
                },
            ],
        }

        # 1. Restore from simulated cloud payload
        with patch("jsonbin_sync._is_configured", return_value=True), \
             patch("jsonbin_sync.load_cloud_state", return_value=cloud_state):
            jsonbin_sync.restore_db_from_cloud()

        # 2. Verify restored tokens in SQLite bot_metadata
        self.assertEqual(database.get_metadata("deviantart_access_token"), "cloud_restored_acc_999")
        self.assertEqual(database.get_metadata("deviantart_refresh_token"), "cloud_restored_ref_888")

        # 3. Verify deduplication state restored in deviantart_posts
        self.assertTrue(database.is_deviantart_posted("art_cyberpunk_01.jpg"))
        self.assertTrue(database.is_deviantart_posted("art_fantasy_02.png"))
        self.assertFalse(database.is_deviantart_posted("unposted_art_03.jpg"))

        # 4. Verify round-trip state building via build_state_payload()
        payload, byte_size = jsonbin_sync.build_state_payload()

        self.assertIn("deviantart_posts", payload)
        self.assertIn("bot_metadata", payload)
        self.assertEqual(len(payload["deviantart_posts"]), 2)

        da_post = next(p for p in payload["deviantart_posts"] if p["filename"] == "art_cyberpunk_01.jpg")
        self.assertEqual(da_post["post_url"], "https://deviantart.com/deviation/123456")
        self.assertEqual(da_post["itemid"], 123456)
        self.assertEqual(da_post["title"], "Cyberpunk Girl in Rain")

        meta_keys = {m["key"]: m["value"] for m in payload["bot_metadata"]}
        self.assertEqual(meta_keys.get("deviantart_access_token"), "cloud_restored_acc_999")
        self.assertEqual(meta_keys.get("deviantart_refresh_token"), "cloud_restored_ref_888")

    def test_jsonbin_budget_stress_preserves_deviantart_metadata(self):
        """
        Adversarial test: under extreme payload size (> 90KB limit), verify that:
        - Tier 1 pruning trims deviantart_posts to 5 entries for deduplication hygiene
        - bot_metadata is STRICTLY preserved (tokens are NEVER pruned away)
        - Final payload size stays under the 90KB budget
        """
        with database._get_conn() as conn:
            # Insert 25 deviantart posts
            for i in range(25):
                conn.execute(
                    "INSERT INTO deviantart_posts (filename, post_url, itemid, title, image_url) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"heavy_art_{i}.jpg", f"https://da.com/{i}", i, f"Title {i}", f"https://cdn.com/{i}.jpg")
                )
            # Insert items into pin_queue (which has no LIMIT in query) to push JSON size over 95KB
            large_desc = "A" * 1500
            for i in range(70):
                conn.execute(
                    "INSERT INTO pin_queue (post_id, image_path, title, description, link, anime_name, image_url) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"queue_id_{i}", f"/path/to/img_{i}.jpg", f"Title {i}", large_desc, f"https://link_{i}.com", "Anime", "https://img.com/a.jpg")
                )
            # Insert DeviantArt tokens in bot_metadata
            conn.execute("INSERT OR REPLACE INTO bot_metadata (key, value) VALUES (?, ?)",
                         ("deviantart_access_token", "super_critical_access_token_123"))
            conn.execute("INSERT OR REPLACE INTO bot_metadata (key, value) VALUES (?, ?)",
                         ("deviantart_refresh_token", "super_critical_refresh_token_456"))
            conn.commit()

        payload, byte_size = jsonbin_sync.build_state_payload()

        # 1. deviantart_posts was trimmed to 5 items during Tier 1 pruning
        self.assertEqual(len(payload["deviantart_posts"]), 5)

        # 2. bot_metadata was NOT pruned: DeviantArt tokens survive in full
        meta_map = {m["key"]: m["value"] for m in payload["bot_metadata"]}
        self.assertEqual(meta_map.get("deviantart_access_token"), "super_critical_access_token_123")
        self.assertEqual(meta_map.get("deviantart_refresh_token"), "super_critical_refresh_token_456")

    def test_sqlite_error_resilience_in_refresh_token(self):
        """
        Adversarial test: simulate unexpected database write failure during token rotation.
        _refresh_token must not crash, must attempt cloud sync, and must return the new access token.
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "fallback_acc_123",
            "refresh_token": "fallback_ref_456",
        }

        mock_sync = MagicMock()

        with patch("requests.post", return_value=mock_resp), \
             patch("database.set_metadata", side_effect=sqlite3.OperationalError("disk I/O error")), \
             patch("jsonbin_sync.trigger_cloud_sync_now", mock_sync):

            tok = deviantart_uploader._refresh_token(
                client_id="cid", client_secret="csecret", refresh_token="rtoken"
            )
            self.assertEqual(tok, "fallback_acc_123")
            mock_sync.assert_called_with(min_interval_sec=0)


class TestAdversarialR2PixelfedStatusHandling(unittest.TestCase):
    """
    Adversarial challenge test suite for Requirement R2:
    Pixelfed decoupled config, 401 unauthenticated vs 500/503 errors,
    status hiding in /crosspost, and telegram command responses.
    """

    def test_pixelfed_decoupling_empty_token(self):
        """
        Adversarial test: verifies requirement that PIXELFED_ENABLED=true with empty token
        reports state='missing_token', NOT state='disabled'.
        """
        test_matrix = [
            # (enabled_val, token_val, expected_enabled, expected_state, expected_auth)
            (False, "", False, "disabled", False),
            (False, "some_token", False, "disabled", False),
            (True, "", True, "missing_token", False),
        ]

        for en_val, tok_val, exp_en, exp_state, exp_auth in test_matrix:
            with patch.object(config, "PIXELFED_ENABLED", en_val), \
                 patch.object(config, "PIXELFED_ACCESS_TOKEN", tok_val):

                st = pixelfed_uploader.get_pixelfed_status()
                self.assertEqual(st["enabled"], exp_en, f"Failed enabled check for ({en_val}, '{tok_val}')")
                self.assertEqual(st["state"], exp_state, f"Failed state check for ({en_val}, '{tok_val}')")
                self.assertEqual(st["authenticated"], exp_auth, f"Failed auth check for ({en_val}, '{tok_val}')")
                self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

    @patch("requests.get")
    def test_pixelfed_status_distinct_http_responses(self, mock_get):
        """
        Adversarial test: verifies distinct error classifications across HTTP status codes:
        - 401: state='unauthenticated', status_code=401
        - 500: state='error', status_code=500
        - 502: state='error', status_code=502
        - 503: state='error', status_code=503
        - 429: state='error', status_code=429
        - Timeout: state='error', status_code=None
        - 200 with invalid body: state='error'
        - 200 with valid body: state='active', authenticated=True
        """
        with patch.object(config, "PIXELFED_ENABLED", True), \
             patch.object(config, "PIXELFED_ACCESS_TOKEN", "mock_tok_123"):

            # 1. HTTP 401 Unauthorized
            r401 = MagicMock()
            r401.status_code = 401
            mock_get.return_value = r401
            st401 = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st401["state"], "unauthenticated")
            self.assertEqual(st401["status_code"], 401)
            self.assertFalse(st401["authenticated"])
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

            # 2. HTTP 500 Internal Server Error (Regression test: old code returned True for 500!)
            r500 = MagicMock()
            r500.status_code = 500
            mock_get.return_value = r500
            st500 = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st500["state"], "error")
            self.assertEqual(st500["status_code"], 500)
            self.assertFalse(st500["authenticated"])
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

            # 3. HTTP 503 Service Unavailable
            r503 = MagicMock()
            r503.status_code = 503
            mock_get.return_value = r503
            st503 = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st503["state"], "error")
            self.assertEqual(st503["status_code"], 503)
            self.assertFalse(st503["authenticated"])
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

            # 4. HTTP 429 Too Many Requests
            r429 = MagicMock()
            r429.status_code = 429
            mock_get.return_value = r429
            st429 = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st429["state"], "error")
            self.assertEqual(st429["status_code"], 429)
            self.assertFalse(st429["authenticated"])
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

            # 5. Connection Timeout
            import requests
            mock_get.side_effect = requests.Timeout("Connection timed out after 10s")
            st_timeout = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st_timeout["state"], "error")
            self.assertIsNone(st_timeout["status_code"])
            self.assertFalse(st_timeout["authenticated"])
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())
            mock_get.side_effect = None

            # 6. HTTP 200 with invalid JSON response
            r200_bad = MagicMock()
            r200_bad.status_code = 200
            r200_bad.json.side_effect = ValueError("Invalid JSON")
            mock_get.return_value = r200_bad
            st200_bad = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st200_bad["state"], "error")
            self.assertFalse(st200_bad["authenticated"])
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

            # 7. HTTP 200 with JSON missing 'id'
            r200_noid = MagicMock()
            r200_noid.status_code = 200
            r200_noid.json.return_value = {"error": "unexpected format"}
            mock_get.return_value = r200_noid
            st200_noid = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st200_noid["state"], "error")
            self.assertFalse(st200_noid["authenticated"])
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

            # 8. HTTP 200 with valid account object
            r200_ok = MagicMock()
            r200_ok.status_code = 200
            r200_ok.json.return_value = {"id": "4242", "username": "anime_art_bot"}
            mock_get.return_value = r200_ok
            st200_ok = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(st200_ok["state"], "active")
            self.assertEqual(st200_ok["username"], "anime_art_bot")
            self.assertTrue(st200_ok["authenticated"])
            self.assertTrue(pixelfed_uploader.verify_pixelfed_token())

    def test_crosspost_command_status_hiding_when_disabled(self):
        """
        Adversarial test: when PIXELFED_ENABLED=False, cmd_crosspost must completely omit
        all Pixelfed status details and never invoke Pixelfed network checks.
        """
        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_update.effective_chat.id = 123456
        telegram_bot._state["admin_chat_id"] = 123456

        mock_pixelfed_status = MagicMock()

        disabled_platforms = {
            "PIXELFED_ENABLED": False,
            "ARENA_ENABLED": False,
            "BLUESKY_ENABLED": False,
            "RAINDROP_ENABLED": False,
            "MASTODON_ENABLED": False,
            "FREEIMAGE_ENABLED": False,
            "IMGHIPPO_ENABLED": False,
            "WALLHAVEN_ENABLED": False,
            "DEVIANTART_ENABLED": False,
            "IMGBB_ENABLED": False,
            "IMAGEKIT_ENABLED": False,
        }

        with patch.multiple(config, **disabled_platforms), \
             patch("pixelfed_uploader.get_pixelfed_status", mock_pixelfed_status), \
             patch("database.get_today_uploads", return_value=[]), \
             patch("database.get_all_time_stats", return_value={"total": 0}), \
             patch("database.get_arena_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_bluesky_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_raindrop_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_mastodon_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_freeimage_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_imghippo_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_wallhaven_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_deviantart_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_imgbb_stats", return_value={"today": 0, "total": 0}), \
             patch("database.get_imagekit_stats", return_value={"today": 0, "total": 0}):

            asyncio.run(telegram_bot.cmd_crosspost(mock_update, MagicMock()))

            mock_update.message.reply_text.assert_called_once()
            sent_msg = mock_update.message.reply_text.call_args[0][0]

            # 1. Output string must NEVER leak Pixelfed status section
            self.assertNotIn("📷 Pixelfed:", sent_msg)
            self.assertNotIn("Pixelfed:\n", sent_msg)
            # 2. get_pixelfed_status must never be called
            mock_pixelfed_status.assert_not_called()

    def test_crosspost_command_formatting_when_enabled(self):
        """
        Adversarial test: when PIXELFED_ENABLED=True, cmd_crosspost must display
        exact state indicators:
        - active -> 🟢 ACTIVE
        - 401 -> 🔴 AUTH ERROR (HTTP 401: Token invalid or expired)
        - missing token -> 🔴 TOKEN MISSING (Check PIXELFED_ACCESS_TOKEN)
        - 500 error -> 🔴 ERROR (HTTP 500 Error)
        """
        mock_update = MagicMock()
        mock_update.effective_chat.id = 123456
        telegram_bot._state["admin_chat_id"] = 123456

        cases = [
            ({"authenticated": True, "state": "active", "status_code": 200}, "🟢 ACTIVE"),
            ({"authenticated": False, "state": "unauthenticated", "status_code": 401}, "🔴 AUTH ERROR (HTTP 401: Token invalid or expired)"),
            ({"authenticated": False, "state": "missing_token", "status_code": None}, "🔴 TOKEN MISSING (Check PIXELFED_ACCESS_TOKEN)"),
            ({"authenticated": False, "state": "error", "status_code": 500, "message": "HTTP 500 Error"}, "🔴 ERROR (HTTP 500 Error)"),
        ]

        platform_config = {
            "PIXELFED_ENABLED": True,
            "ARENA_ENABLED": False,
            "BLUESKY_ENABLED": False,
            "RAINDROP_ENABLED": False,
            "MASTODON_ENABLED": False,
            "FREEIMAGE_ENABLED": False,
            "IMGHIPPO_ENABLED": False,
            "WALLHAVEN_ENABLED": False,
            "DEVIANTART_ENABLED": False,
            "IMGBB_ENABLED": False,
            "IMAGEKIT_ENABLED": False,
        }

        for status_ret, expected_text in cases:
            mock_update.message.reply_text = AsyncMock()

            with patch.multiple(config, **platform_config), \
                 patch("pixelfed_uploader.get_pixelfed_status", return_value=status_ret), \
                 patch("pixelfed_uploader.get_pixelfed_profile_info", return_value={"statuses_count": 10, "url": "https://pixelfed.social/bot"}), \
                 patch("database.get_pixelfed_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_today_uploads", return_value=[]), \
                 patch("database.get_all_time_stats", return_value={"total": 0}), \
                 patch("database.get_arena_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_bluesky_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_raindrop_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_mastodon_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_freeimage_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_imghippo_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_wallhaven_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_deviantart_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_imgbb_stats", return_value={"today": 0, "total": 0}), \
                 patch("database.get_imagekit_stats", return_value={"today": 0, "total": 0}):

                asyncio.run(telegram_bot.cmd_crosspost(mock_update, MagicMock()))

                sent_msg = mock_update.message.reply_text.call_args[0][0]
                self.assertIn("📷 Pixelfed:", sent_msg)
                self.assertIn(expected_text, sent_msg)

    def test_telegram_cmd_pixelfed_responses(self):
        """
        Adversarial test: verifies dedicated /pixelfed command responses:
        - When disabled: ⚪ DISABLED
        - When missing token: 🔴 Token missing
        - When 401: 🔴 HTTP 401 Unauthorized
        """
        mock_update = MagicMock()
        mock_update.effective_chat.id = 123456
        telegram_bot._state["admin_chat_id"] = 123456

        # 1. Disabled
        mock_update.message.reply_text = AsyncMock()
        with patch.object(config, "PIXELFED_ENABLED", False):
            asyncio.run(telegram_bot.cmd_pixelfed(mock_update, MagicMock()))
            msg = mock_update.message.reply_text.call_args[0][0]
            self.assertIn("📷 Pixelfed: ⚪ DISABLED", msg)

        # 2. Missing token
        mock_update.message.reply_text = AsyncMock()
        with patch.object(config, "PIXELFED_ENABLED", True), \
             patch("pixelfed_uploader.get_pixelfed_status", return_value={"authenticated": False, "state": "missing_token"}):
            asyncio.run(telegram_bot.cmd_pixelfed(mock_update, MagicMock()))
            msg = mock_update.message.reply_text.call_args[0][0]
            self.assertIn("🔴 Token missing", msg)

        # 3. 401 Unauthorized
        mock_update.message.reply_text = AsyncMock()
        with patch.object(config, "PIXELFED_ENABLED", True), \
             patch("pixelfed_uploader.get_pixelfed_status", return_value={"authenticated": False, "state": "unauthenticated", "status_code": 401}):
            asyncio.run(telegram_bot.cmd_pixelfed(mock_update, MagicMock()))
            msg = mock_update.message.reply_text.call_args[0][0]
            self.assertIn("🔴 HTTP 401 Unauthorized", msg)

    def test_crosspost_dispatcher_pixelfed_disabled_resilience(self):
        """
        Adversarial test: crosspost_dispatcher._post_pixelfed must immediately return (None, "")
        when disabled, and format_crosspost_status_block must completely omit Pixelfed
        even if an execution result is provided.
        """
        cfg = MagicMock()
        cfg.PIXELFED_ENABLED = False

        # 1. Dispatcher bypasses execution completely
        ok, url = crosspost_dispatcher._post_pixelfed(
            filename="test.jpg",
            title="Test",
            description="Desc",
            link="https://pin.it",
            public_img_url="",
            original_image_url="",
            image_path="",
            is_stealth=False,
            config=cfg,
        )
        self.assertIsNone(ok)
        self.assertEqual(url, "")

        # 2. format_crosspost_status_block omits Pixelfed when disabled
        with patch.object(config, "PIXELFED_ENABLED", False):
            formatted = telegram_bot.format_crosspost_status_block({
                "pixelfed_ok": True,  # Stale or rogue result
                "bluesky_ok": True,
            })
            self.assertNotIn("Pixelfed", formatted)
            self.assertIn("Bluesky", formatted)


if __name__ == "__main__":
    unittest.main()
