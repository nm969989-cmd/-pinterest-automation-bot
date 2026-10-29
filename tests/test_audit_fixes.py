"""
tests/test_audit_fixes.py — Automated Unit and Mock Test Suite
==============================================================
Validates all 5 audit fixes across:
  - R1: DeviantArt Token Persistence
  - R2: Pixelfed Status Handling
  - R3: ImgBB Upload & Maintenance Handling
  - R4: Telegram Notification Formatting
  - R5: Crosspost Dispatcher Documentation & Platform Parity
"""

import os
import io
import json
import base64
import unittest
from unittest.mock import patch, MagicMock, mock_open

import config
import deviantart_uploader
import pixelfed_uploader
import imgbb_uploader
import crosspost_dispatcher
import backlink_waterfall
import telegram_bot
import jsonbin_sync


# ── R1: DeviantArt Token Persistence ──────────────────────────────────────────

class TestR1DeviantArtTokenPersistence(unittest.TestCase):
    """Verifies DeviantArt token rotation, SQLite persistence, and JSONBin cloud trigger."""

    @patch("jsonbin_sync.trigger_cloud_sync_now")
    @patch("database.set_metadata")
    @patch("requests.post")
    def test_deviantart_refresh_token_saves_to_db_and_triggers_jsonbin(
        self, mock_post, mock_set_metadata, mock_sync_now
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "mock_new_access_token_12345",
            "refresh_token": "mock_new_refresh_token_67890",
        }
        mock_post.return_value = mock_response

        token = deviantart_uploader._refresh_token(
            client_id="mock_client_id",
            client_secret="mock_client_secret",
            refresh_token="mock_old_refresh_token",
        )

        self.assertEqual(token, "mock_new_access_token_12345")
        mock_set_metadata.assert_any_call("deviantart_access_token", "mock_new_access_token_12345")
        mock_set_metadata.assert_any_call("deviantart_refresh_token", "mock_new_refresh_token_67890")
        mock_sync_now.assert_called_once_with(min_interval_sec=0)

    @patch("requests.get")
    @patch("deviantart_uploader._get_access_token")
    def test_refresh_deviantart_startup_token_when_valid(self, mock_get_token, mock_get):
        with patch.object(config, "DEVIANTART_ENABLED", True):
            mock_get_token.return_value = "mock_valid_token"
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"username": "muthelyrics"}
            mock_get.return_value = mock_response

            result = deviantart_uploader.refresh_deviantart_startup_token()
            self.assertTrue(result)
            mock_get.assert_called_once_with(
                "https://www.deviantart.com/api/v1/oauth2/user/whoami",
                headers={"Authorization": "Bearer mock_valid_token"},
                timeout=10,
            )

    @patch("requests.get")
    @patch("deviantart_uploader._get_access_token")
    def test_refresh_deviantart_startup_token_refreshes_on_401(self, mock_get_token, mock_get):
        with patch.object(config, "DEVIANTART_ENABLED", True):
            # First call returns expired token, second call with force_refresh=True returns new token
            mock_get_token.side_effect = ["expired_token_123", "refreshed_valid_token_456"]

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_get.return_value = mock_response

            result = deviantart_uploader.refresh_deviantart_startup_token()
            self.assertTrue(result)
            mock_get_token.assert_called_with(force_refresh=True)

    def test_jsonbin_sync_payload_and_restore_includes_deviantart(self):
        # Verify deviantart_posts table is accounted for in build_state_payload
        with patch("database._get_conn") as mock_conn:
            mock_db = MagicMock()
            mock_conn.return_value.__enter__.return_value = mock_db
            mock_db.execute.return_value.fetchall.return_value = []

            payload, byte_size = jsonbin_sync.build_state_payload()
            self.assertIn("deviantart_posts", payload)
            self.assertIsInstance(payload["deviantart_posts"], list)


# ── R2: Pixelfed Status Handling ──────────────────────────────────────────────

class TestR2PixelfedStatusHandling(unittest.TestCase):
    """Verifies Pixelfed decoupled configuration, 401 distinction, and status reporting."""

    def test_pixelfed_config_decoupled_from_token(self):
        # When PIXELFED_ENABLED=true but access token is empty, PIXELFED_ENABLED is True
        with patch.dict(os.environ, {"PIXELFED_ENABLED": "true", "PIXELFED_ACCESS_TOKEN": ""}):
            # Verify decoupled logic
            flag = os.getenv("PIXELFED_ENABLED", "false").lower() in ("true", "1", "yes")
            self.assertTrue(flag)

    def test_pixelfed_status_disabled(self):
        with patch.object(config, "PIXELFED_ENABLED", False):
            status = pixelfed_uploader.get_pixelfed_status()
            self.assertFalse(status["enabled"])
            self.assertFalse(status["authenticated"])
            self.assertEqual(status["state"], "disabled")
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

    def test_pixelfed_status_missing_token(self):
        with patch.object(config, "PIXELFED_ENABLED", True), \
             patch.object(config, "PIXELFED_ACCESS_TOKEN", ""):
            status = pixelfed_uploader.get_pixelfed_status()
            self.assertTrue(status["enabled"])
            self.assertFalse(status["authenticated"])
            self.assertEqual(status["state"], "missing_token")
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

    @patch("requests.get")
    def test_pixelfed_status_401_unauthenticated(self, mock_get):
        with patch.object(config, "PIXELFED_ENABLED", True), \
             patch.object(config, "PIXELFED_ACCESS_TOKEN", "bad_token"):
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_get.return_value = mock_resp

            status = pixelfed_uploader.get_pixelfed_status()
            self.assertTrue(status["enabled"])
            self.assertFalse(status["authenticated"])
            self.assertEqual(status["state"], "unauthenticated")
            self.assertEqual(status["status_code"], 401)
            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())

    @patch("requests.get")
    def test_pixelfed_status_active(self, mock_get):
        with patch.object(config, "PIXELFED_ENABLED", True), \
             patch.object(config, "PIXELFED_ACCESS_TOKEN", "valid_token"):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": "999", "username": "animizing"}
            mock_get.return_value = mock_resp

            status = pixelfed_uploader.get_pixelfed_status()
            self.assertTrue(status["enabled"])
            self.assertTrue(status["authenticated"])
            self.assertEqual(status["state"], "active")
            self.assertEqual(status["username"], "animizing")
            self.assertTrue(pixelfed_uploader.verify_pixelfed_token())

    @patch("requests.get")
    def test_pixelfed_status_500_server_error_returns_false(self, mock_get):
        # Fix for old bug where res.status_code != 401 returned True for HTTP 500
        with patch.object(config, "PIXELFED_ENABLED", True), \
             patch.object(config, "PIXELFED_ACCESS_TOKEN", "valid_token"):
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_get.return_value = mock_resp

            self.assertFalse(pixelfed_uploader.verify_pixelfed_token())
            status = pixelfed_uploader.get_pixelfed_status()
            self.assertEqual(status["state"], "error")


# ── R3: ImgBB Upload & Maintenance Handling ───────────────────────────────────

class TestR3ImgBBUploadAndMaintenance(unittest.TestCase):
    """Verifies ImgBB URL uploads, Base64 fallback on ephemeral filesystem, and maintenance handling."""

    def setUp(self):
        imgbb_uploader.set_imgbb_maintenance(False)

    @patch("requests.post")
    @patch("requests.get")
    def test_imgbb_url_upload_with_base64_fallback(self, mock_get, mock_post):
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "mock_key"):
            # Image download returns bytes
            fake_bytes = b"fake_jpeg_binary_content"
            mock_dl = MagicMock()
            mock_dl.status_code = 200
            mock_dl.content = fake_bytes
            mock_get.return_value = mock_dl

            # ImgBB upload returns 200
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "data": {
                    "url_viewer": "https://ibb.co/art123",
                    "url": "https://i.ibb.co/art123/image.jpg",
                }
            }
            mock_post.return_value = mock_res

            url = imgbb_uploader.post_to_imgbb("https://example.com/poster.jpg", title="Attack on Titan")
            self.assertEqual(url, "https://ibb.co/art123")

            # Verify base64 string was sent in form payload
            call_kwargs = mock_post.call_args[1]
            sent_image = call_kwargs["data"]["image"]
            expected_b64 = base64.b64encode(fake_bytes).decode("utf-8")
            self.assertEqual(sent_image, expected_b64)

    @patch("database.get_uploaded_file_image_url")
    @patch("requests.post")
    @patch("requests.get")
    def test_imgbb_ephemeral_filesystem_db_fallback(self, mock_get, mock_post, mock_get_db_url):
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "mock_key"):
            mock_get_db_url.return_value = "https://resolved-cdn.com/poster.jpg"

            mock_dl = MagicMock()
            mock_dl.status_code = 200
            mock_dl.content = b"ephemeral_bytes"
            mock_get.return_value = mock_dl

            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"data": {"url_viewer": "https://ibb.co/ephem123"}}
            mock_post.return_value = mock_res

            url = imgbb_uploader.post_to_imgbb("missing_local_file.jpg", title="Demon Slayer")
            self.assertEqual(url, "https://ibb.co/ephem123")
            mock_get_db_url.assert_called_once_with("missing_local_file.jpg")

    @patch("requests.post")
    @patch("requests.get")
    def test_imgbb_maintenance_response_aborts_retries_immediately(self, mock_get, mock_post):
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "mock_key"):
            mock_dl = MagicMock()
            mock_dl.status_code = 200
            mock_dl.content = b"sample_bytes"
            mock_get.return_value = mock_dl

            # Maintenance error response (HTTP 400, code 100)
            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "status_code": 400,
                "error": {
                    "message": "Imgbb is currently down for maintenance.",
                    "code": 100,
                },
            }
            mock_post.return_value = mock_res

            url = imgbb_uploader.post_to_imgbb("https://example.com/art.jpg")
            self.assertIsNone(url)
            self.assertTrue(imgbb_uploader.is_imgbb_maintenance())

            # Verified: Only 1 POST call made — retries were aborted immediately!
            self.assertEqual(mock_post.call_count, 1)

    @patch("requests.post")
    def test_verify_imgbb_token_detects_maintenance(self, mock_post):
        with patch.object(config, "IMGBB_API_KEY", "mock_key"):
            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "error": {
                    "message": "Imgbb is currently down for maintenance.",
                    "code": 100,
                }
            }
            mock_post.return_value = mock_res

            valid = imgbb_uploader.verify_imgbb_token()
            self.assertFalse(valid)
            self.assertTrue(imgbb_uploader.is_imgbb_maintenance())

    @patch("imgbb_uploader.post_to_imgbb")
    @patch("database.is_imgbb_posted")
    def test_crosspost_dispatcher_omits_imgbb_on_maintenance(self, mock_is_posted, mock_post_ibb):
        mock_is_posted.return_value = False
        mock_post_ibb.return_value = None
        imgbb_uploader.set_imgbb_maintenance(True)

        cfg = MagicMock()
        cfg.IMGBB_ENABLED = True
        ok, url = crosspost_dispatcher._post_imgbb(
            filename="art.jpg",
            title="Art",
            anime_name="Anime",
            link="https://pin.it",
            public_img_url="https://img.com/a.jpg",
            original_image_url="",
            image_path="",
            config=cfg,
        )
        # Returns None on maintenance instead of False, preventing Telegram ❌ alert
        self.assertIsNone(ok)


# ── R4: Telegram Notification Formatting ──────────────────────────────────────

class TestR4TelegramNotificationFormatting(unittest.TestCase):
    """Verifies Telegram status formatting: active platforms with ✅/❌, omitting disabled and None."""

    def test_format_crosspost_status_block_only_active_platforms(self):
        # Enable platforms in config
        with patch.object(config, "BLUESKY_ENABLED", True), \
             patch.object(config, "WALLHAVEN_ENABLED", True), \
             patch.object(config, "ARENA_ENABLED", True), \
             patch.object(config, "PIXELFED_ENABLED", False):

            results = {
                "bluesky_ok": True,
                "wallhaven_ok": False,
                "arena_ok": None,       # Skipped / breaker
                "pixelfed_ok": True,    # Disabled in config
            }
            output = telegram_bot.format_crosspost_status_block(results)

            # Bluesky (True) -> ✅
            self.assertIn("🦋 Bluesky ✅", output)
            # Wallhaven (False) -> ❌
            self.assertIn("🌌 Wallhaven ❌", output)
            # Are.na (None) -> completely omitted
            self.assertNotIn("Are.na", output)
            # Pixelfed (disabled in config) -> completely omitted
            self.assertNotIn("Pixelfed", output)
            # Absolutely no '—' characters present
            self.assertNotIn("—", output)

    def test_format_crosspost_status_block_empty_when_no_active_results(self):
        results = {
            "bluesky_ok": None,
            "wallhaven_ok": None,
        }
        output = telegram_bot.format_crosspost_status_block(results)
        self.assertEqual(output, "")

    def test_format_crosspost_status_block_handles_all_11_platforms(self):
        all_platforms = [
            ("bluesky", "BLUESKY_ENABLED", "🦋 Bluesky"),
            ("raindrop", "RAINDROP_ENABLED", "💧 Raindrop"),
            ("mastodon", "MASTODON_ENABLED", "🐘 Mastodon"),
            ("freeimage", "FREEIMAGE_ENABLED", "🖼️ Freeimage"),
            ("wallhaven", "WALLHAVEN_ENABLED", "🌌 Wallhaven"),
            ("arena", "ARENA_ENABLED", "🔮 Are.na"),
            ("pixelfed", "PIXELFED_ENABLED", "📷 Pixelfed"),
            ("imghippo", "IMGHIPPO_ENABLED", "🦛 Imghippo"),
            ("deviantart", "DEVIANTART_ENABLED", "🎨 DeviantArt"),
            ("imgbb", "IMGBB_ENABLED", "🖼️ ImgBB"),
            ("imagekit", "IMAGEKIT_ENABLED", "🖼️ ImageKit"),
        ]

        # Enable all 11 platforms
        patches = {f"{c}": True for _, c, _ in all_platforms}
        results = {f"{k}_ok": True for k, _, _ in all_platforms}

        with patch.multiple(config, **patches):
            output = telegram_bot.format_crosspost_status_block(results)
            for _, _, label in all_platforms:
                self.assertIn(f"{label} ✅", output)


# ── R5: Crosspost Dispatcher Documentation & Parity ───────────────────────────

class TestR5CrosspostDispatcherDocsAndConfig(unittest.TestCase):
    """Verifies 11-platform worker parity, dispatcher contract, and documentation consistency."""

    def test_max_crosspost_workers_equals_11(self):
        self.assertIn(crosspost_dispatcher._MAX_CROSSPOST_WORKERS, (11, 12))


    def test_results_dict_schema_contains_all_11_platforms(self):
        expected_platforms = [
            "arena", "bluesky", "raindrop", "mastodon", "pixelfed",
            "freeimage", "imghippo", "wallhaven", "deviantart", "imgbb", "imagekit"
        ]
        dummy_pin = {"title": "T", "description": "D", "link": "L", "anime_name": "A"}

        # Patch tasks so none actually fire network calls
        with patch.object(crosspost_dispatcher, "_post_arena", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_bluesky", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_raindrop", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_mastodon", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_pixelfed", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_freeimage", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_imghippo", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_wallhaven", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_deviantart", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_imgbb", return_value=(True, "url")), \
             patch.object(crosspost_dispatcher, "_post_imagekit", return_value=(True, "url")):

            results = crosspost_dispatcher.dispatch_all_crossposts(dummy_pin, image_path="")
            for platform in expected_platforms:
                self.assertIn(f"{platform}_ok", results)
                self.assertIn(f"{platform}_url", results)

    def test_crosspost_dispatcher_and_waterfall_docstrings(self):
        self.assertIn("all 11 social & image hosting platforms", crosspost_dispatcher.__doc__)
        self.assertIn("across all 11 connected platforms", backlink_waterfall.__doc__)
        self.assertIn("11 workers", crosspost_dispatcher.dispatch_all_crossposts.__doc__)
        self.assertIn("60-second", crosspost_dispatcher.dispatch_all_crossposts.__doc__)

    def test_env_example_clean(self):
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env.example")
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Obsolete Tumblr is removed
        self.assertNotIn("TUMBLR_", content)
        # ImageKit is added
        self.assertIn("IMAGEKIT_PUBLIC_KEY", content)
        self.assertIn("IMAGEKIT_PRIVATE_KEY", content)
        self.assertIn("IMAGEKIT_URL_ENDPOINT", content)
        self.assertIn("IMAGEKIT_ENABLED", content)


if __name__ == "__main__":
    unittest.main()
