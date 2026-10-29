"""
tests/test_adversarial_m1_2.py
==============================
Adversarial challenge test suite for Milestone 1:
- R3: ImgBB Upload & Maintenance Handling (ephemeral filesystems, Base64 fallback, maintenance detection, retries)
- R4: Telegram Notification Formatting (active platform indicators, exclusion of disabled/skipped, fuzzing)
- R5: Crosspost Dispatcher Documentation & Parity (11-platform ecosystem alignment)
"""

import os
import sys
import base64
import random
import unittest
from unittest.mock import patch, MagicMock, call

# Ensure workspace root is in sys.path
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

import config
import imgbb_uploader
import crosspost_dispatcher
import telegram_bot
import backlink_waterfall


class TestAdversarialR3ImgBBUploadAndMaintenance(unittest.TestCase):
    """Adversarial stress-testing of ImgBB upload and maintenance handling."""

    def setUp(self):
        imgbb_uploader.set_imgbb_maintenance(False)

    def tearDown(self):
        imgbb_uploader.set_imgbb_maintenance(False)

    # ── Ephemeral Filesystem Challenges ──────────────────────────────────────

    @patch("database.get_uploaded_file_image_url")
    @patch("requests.post")
    @patch("requests.get")
    def test_ephemeral_missing_file_with_valid_db_resolution(self, mock_get, mock_post, mock_get_db):
        """Challenge: local file is missing on ephemeral container, but DB maps filename to CDN URL."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            mock_get_db.return_value = "https://cdn.animanoizing.com/posters/aot_001.jpg"

            # CDN download succeeds
            fake_bytes = b"fake_aot_image_data_4k"
            mock_dl = MagicMock()
            mock_dl.status_code = 200
            mock_dl.content = fake_bytes
            mock_get.return_value = mock_dl

            # ImgBB upload succeeds
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "data": {
                    "url_viewer": "https://ibb.co/liveAot",
                    "url": "https://i.ibb.co/liveAot/poster.jpg"
                }
            }
            mock_post.return_value = mock_res

            # Call with a non-existent local file path
            result = imgbb_uploader.post_to_imgbb("/ephemeral/temp/missing_local_file.jpg", title="Attack on Titan")

            # Must resolve filename against database
            mock_get_db.assert_called_once_with("missing_local_file.jpg")
            # Must fetch remote URL
            mock_get.assert_called_once_with("https://cdn.animanoizing.com/posters/aot_001.jpg", headers=imgbb_uploader._HEADERS, timeout=20)
            # Must upload base64 of downloaded bytes
            self.assertEqual(result, "https://ibb.co/liveAot")
            call_data = mock_post.call_args[1]["data"]
            self.assertEqual(call_data["image"], base64.b64encode(fake_bytes).decode("utf-8"))

    @patch("database.get_uploaded_file_image_url")
    @patch("requests.post")
    def test_ephemeral_missing_file_without_db_resolution(self, mock_post, mock_get_db):
        """Challenge: local file is missing and DB has no record -> must return None without HTTP post."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            mock_get_db.return_value = None

            result = imgbb_uploader.post_to_imgbb("/tmp/phantom_file.jpg")
            self.assertIsNone(result)
            mock_post.assert_not_called()

    @patch("database.get_uploaded_file_image_url")
    @patch("requests.post")
    def test_ephemeral_missing_file_db_raises_operational_error(self, mock_post, mock_get_db):
        """Challenge: SQLite operational error or locked DB during ephemeral lookup must be handled gracefully."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            import sqlite3
            mock_get_db.side_effect = sqlite3.OperationalError("database is locked")

            result = imgbb_uploader.post_to_imgbb("/tmp/locked_db_file.jpg")
            self.assertIsNone(result)
            mock_post.assert_not_called()

    @patch("requests.post")
    def test_empty_or_none_image_path(self, mock_post):
        """Challenge: empty string or None passed as image_path."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            self.assertIsNone(imgbb_uploader.post_to_imgbb(""))
            self.assertIsNone(imgbb_uploader.post_to_imgbb(None))
            mock_post.assert_not_called()

    # ── Base64 Download Fallback Challenges ──────────────────────────────────

    @patch("requests.post")
    @patch("requests.get")
    def test_base64_fallback_when_cdn_download_raises_connection_error(self, mock_get, mock_post):
        """Challenge: CDN cannot be downloaded to memory (connection error) -> must fallback to posting direct URL."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            import requests.exceptions
            mock_get.side_effect = requests.exceptions.ConnectionError("CDN host timed out")

            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"data": {"url_viewer": "https://ibb.co/directUrlViewer"}}
            mock_post.return_value = mock_res

            cdn_url = "https://images.example.com/art_poster.png"
            result = imgbb_uploader.post_to_imgbb(cdn_url, title="Solo Leveling")

            self.assertEqual(result, "https://ibb.co/directUrlViewer")
            # Verify payload was sent with the raw URL as fallback
            call_data = mock_post.call_args[1]["data"]
            self.assertEqual(call_data["image"], cdn_url)

    @patch("requests.post")
    @patch("requests.get")
    def test_base64_fallback_when_cdn_returns_http_404(self, mock_get, mock_post):
        """Challenge: CDN returns 404 -> fallback to passing direct URL to ImgBB."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            mock_dl = MagicMock()
            mock_dl.status_code = 404
            mock_dl.content = b""
            mock_get.return_value = mock_dl

            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {"data": {"url_viewer": "https://ibb.co/404Fallback"}}
            mock_post.return_value = mock_res

            cdn_url = "https://images.example.com/not_found_on_cdn.png"
            result = imgbb_uploader.post_to_imgbb(cdn_url)

            self.assertEqual(result, "https://ibb.co/404Fallback")
            call_data = mock_post.call_args[1]["data"]
            self.assertEqual(call_data["image"], cdn_url)

    # ── ImgBB Maintenance Response Challenges ────────────────────────────────

    @patch("requests.post")
    @patch("requests.get")
    def test_maintenance_official_json_payload(self, mock_get, mock_post):
        """Challenge: official maintenance payload (HTTP 400, code 100, down for maintenance)."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            mock_dl = MagicMock(status_code=200, content=b"bytes")
            mock_get.return_value = mock_dl

            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "status_code": 400,
                "error": {
                    "message": "Imgbb is currently down for maintenance.",
                    "code": 100
                }
            }
            mock_res.text = '{"status_code": 400, "error": {"message": "Imgbb is currently down for maintenance.", "code": 100}}'
            mock_post.return_value = mock_res

            result = imgbb_uploader.post_to_imgbb("https://example.com/art.jpg")
            self.assertIsNone(result)
            self.assertTrue(imgbb_uploader.is_imgbb_maintenance())
            # Retries must be aborted immediately — exactly 1 POST attempt!
            self.assertEqual(mock_post.call_count, 1)

    @patch("requests.post")
    @patch("requests.get")
    def test_maintenance_variant_casing_and_phrasing(self, mock_get, mock_post):
        """Challenge: maintenance payload with uppercase and alternate phrasing."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            mock_dl = MagicMock(status_code=200, content=b"bytes")
            mock_get.return_value = mock_dl

            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "status_code": 400,
                "error": {
                    "message": "API IS CURRENTLY DOWN FOR SCHEDULED MAINTENANCE. CHECK STATUS.",
                    "code": 100
                }
            }
            mock_res.text = '{"error": {"message": "API IS CURRENTLY DOWN FOR SCHEDULED MAINTENANCE. CHECK STATUS."}}'
            mock_post.return_value = mock_res

            result = imgbb_uploader.post_to_imgbb("https://example.com/art.jpg")
            self.assertIsNone(result)
            self.assertTrue(imgbb_uploader.is_imgbb_maintenance())
            self.assertEqual(mock_post.call_count, 1)

    @patch("requests.post")
    @patch("requests.get")
    def test_maintenance_non_json_html_error_response(self, mock_get, mock_post):
        """Challenge: gateway returns HTML page with HTTP 400 during maintenance."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            mock_dl = MagicMock(status_code=200, content=b"bytes")
            mock_get.return_value = mock_dl

            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.side_effect = ValueError("No JSON object could be decoded")
            mock_res.text = "<html><body><h1>Cloudflare: ImgBB is currently down for maintenance</h1></body></html>"
            mock_post.return_value = mock_res

            result = imgbb_uploader.post_to_imgbb("https://example.com/art.jpg")
            self.assertIsNone(result)
            self.assertTrue(imgbb_uploader.is_imgbb_maintenance())
            self.assertEqual(mock_post.call_count, 1)

    @patch("time.sleep")
    @patch("requests.post")
    @patch("requests.get")
    def test_non_maintenance_code_100_does_not_abort_as_maintenance(self, mock_get, mock_post, mock_sleep):
        """Challenge: HTTP 400 code 100 with 'Invalid API v1 key' is NOT maintenance and must exhaust retries."""
        with patch.object(config, "IMGBB_ENABLED", True), \
             patch.object(config, "IMGBB_API_KEY", "test_key_123"):
            mock_dl = MagicMock(status_code=200, content=b"bytes")
            mock_get.return_value = mock_dl

            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "status_code": 400,
                "error": {
                    "message": "Invalid API v1 key.",
                    "code": 100
                }
            }
            mock_res.text = '{"status_code": 400, "error": {"message": "Invalid API v1 key.", "code": 100}}'
            mock_post.return_value = mock_res

            result = imgbb_uploader.post_to_imgbb("https://example.com/art.jpg")
            self.assertIsNone(result)
            # Crucial: Maintenance must NOT be flagged for a bad key
            self.assertFalse(imgbb_uploader.is_imgbb_maintenance())
            # Must exhaust all 3 retry attempts
            self.assertEqual(mock_post.call_count, 3)

    # ── Crosspost Dispatcher Maintenance Handling Challenges ─────────────────

    @patch("imgbb_uploader.post_to_imgbb")
    @patch("database.is_imgbb_posted")
    def test_crosspost_dispatcher_returns_none_on_maintenance(self, mock_is_posted, mock_post_ibb):
        """Challenge: crosspost_dispatcher must return None on maintenance to avoid tripping Telegram alerts."""
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
        self.assertIsNone(ok)
        self.assertEqual(url, "https://muthelyrics.imgbb.com/")

    @patch("imgbb_uploader.post_to_imgbb")
    @patch("database.is_imgbb_posted")
    def test_crosspost_dispatcher_returns_false_on_standard_failure(self, mock_is_posted, mock_post_ibb):
        """Challenge: crosspost_dispatcher returns False (trips alert) on standard failure when NOT in maintenance."""
        mock_is_posted.return_value = False
        mock_post_ibb.return_value = None
        imgbb_uploader.set_imgbb_maintenance(False)

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
        self.assertFalse(ok)
        self.assertEqual(url, "https://muthelyrics.imgbb.com/")


class TestAdversarialR4TelegramNotificationFormatting(unittest.TestCase):
    """Adversarial stress-testing of Telegram notification formatting."""

    ALL_11_PLATFORMS = [
        ("bluesky",    "BLUESKY_ENABLED",    "🦋 Bluesky"),
        ("raindrop",   "RAINDROP_ENABLED",   "💧 Raindrop"),
        ("mastodon",   "MASTODON_ENABLED",   "🐘 Mastodon"),
        ("freeimage",  "FREEIMAGE_ENABLED",  "🖼️ Freeimage"),
        ("wallhaven",  "WALLHAVEN_ENABLED",  "🌌 Wallhaven"),
        ("arena",      "ARENA_ENABLED",      "🔮 Are.na"),
        ("pixelfed",   "PIXELFED_ENABLED",   "📷 Pixelfed"),
        ("imghippo",   "IMGHIPPO_ENABLED",   "🦛 Imghippo"),
        ("deviantart", "DEVIANTART_ENABLED", "🎨 DeviantArt"),
        ("imgbb",      "IMGBB_ENABLED",      "🖼️ ImgBB"),
        ("imagekit",   "IMAGEKIT_ENABLED",   "🖼️ ImageKit"),
    ]

    def test_empty_results_dict_produces_empty_string(self):
        """Challenge: empty dict passed to formatter -> must return empty string."""
        with patch.multiple(config, **{cfg_flag: True for _, cfg_flag, _ in self.ALL_11_PLATFORMS}):
            output = telegram_bot.format_crosspost_status_block({})
            self.assertEqual(output, "")

    def test_all_11_platforms_active_success(self):
        """Challenge: all 11 platforms enabled and True -> all 11 have ✅."""
        with patch.multiple(config, **{cfg_flag: True for _, cfg_flag, _ in self.ALL_11_PLATFORMS}):
            results = {f"{k}_ok": True for k, _, _ in self.ALL_11_PLATFORMS}
            output = telegram_bot.format_crosspost_status_block(results)
            for _, _, label in self.ALL_11_PLATFORMS:
                self.assertIn(f"{label} ✅", output)
                self.assertNotIn(f"{label} ❌", output)
            self.assertNotIn("—", output)

    def test_all_11_platforms_active_failure(self):
        """Challenge: all 11 platforms enabled and False -> all 11 have ❌."""
        with patch.multiple(config, **{cfg_flag: True for _, cfg_flag, _ in self.ALL_11_PLATFORMS}):
            results = {f"{k}_ok": False for k, _, _ in self.ALL_11_PLATFORMS}
            output = telegram_bot.format_crosspost_status_block(results)
            for _, _, label in self.ALL_11_PLATFORMS:
                self.assertIn(f"{label} ❌", output)
                self.assertNotIn(f"{label} ✅", output)
            self.assertNotIn("—", output)

    def test_all_11_platforms_status_none_omits_everything(self):
        """Challenge: all platforms enabled but results are None -> completely empty string."""
        with patch.multiple(config, **{cfg_flag: True for _, cfg_flag, _ in self.ALL_11_PLATFORMS}):
            results = {f"{k}_ok": None for k, _, _ in self.ALL_11_PLATFORMS}
            output = telegram_bot.format_crosspost_status_block(results)
            self.assertEqual(output, "")
            self.assertNotIn("—", output)

    def test_all_platforms_disabled_in_config(self):
        """Challenge: platforms disabled in config must be omitted even if results dict has True."""
        with patch.multiple(config, **{cfg_flag: False for _, cfg_flag, _ in self.ALL_11_PLATFORMS}):
            results = {f"{k}_ok": True for k, _, _ in self.ALL_11_PLATFORMS}
            output = telegram_bot.format_crosspost_status_block(results)
            self.assertEqual(output, "")
            self.assertNotIn("—", output)

    def test_combinatorial_fuzzing_500_iterations(self):
        """
        Adversarial fuzz testing:
        Randomly assigns enabled (True/False) and statuses (True, False, None, missing)
        across 500 iterations.
        Verifies:
        1. Em-dash ('—') NEVER appears under any circumstances.
        2. Any platform with enabled=False is NEVER rendered.
        3. Any platform with status=None is NEVER rendered.
        4. Any platform with enabled=True and status=True renders as '{label} ✅'.
        5. Any platform with enabled=True and status=False renders as '{label} ❌'.
        """
        rng = random.Random(42)

        for iteration in range(500):
            cfg_patches = {}
            results = {}

            expected_items = []
            for key, cfg_flag, label in self.ALL_11_PLATFORMS:
                is_enabled = rng.choice([True, False])
                cfg_patches[cfg_flag] = is_enabled

                status = rng.choice([True, False, None, "UNSET"])
                if status != "UNSET":
                    results[f"{key}_ok"] = status

                if is_enabled and (status in (True, False)):
                    icon = "✅" if status is True else "❌"
                    expected_items.append(f"{label} {icon}")

            with patch.multiple(config, **cfg_patches):
                output = telegram_bot.format_crosspost_status_block(results)

                # Assertion 1: Em-dash NEVER appears
                self.assertNotIn("—", output, f"Iteration {iteration}: em-dash found in output!")

                # Assertion 2 & 3 & 4 & 5: Check expected items
                if not expected_items:
                    self.assertEqual(output, "", f"Iteration {iteration}: expected empty string")
                else:
                    self.assertTrue(output.startswith("\n──────────────────────────\n"), f"Iteration {iteration}: missing divider")
                    for exp in expected_items:
                        self.assertIn(exp, output, f"Iteration {iteration}: expected {exp} in output")

                    # Check that platforms that shouldn't appear do NOT appear
                    for key, cfg_flag, label in self.ALL_11_PLATFORMS:
                        st = results.get(f"{key}_ok")
                        en = cfg_patches[cfg_flag]
                        if not en or st not in (True, False):
                            self.assertNotIn(f"{label} ✅", output)
                            self.assertNotIn(f"{label} ❌", output)


class TestAdversarialR5CrosspostDispatcherDocsAndParity(unittest.TestCase):
    """Adversarial verification of 11-platform ecosystem alignment across docs and configs."""

    ALL_PLATFORMS = [
        "arena", "bluesky", "raindrop", "mastodon", "pixelfed",
        "freeimage", "imghippo", "wallhaven", "deviantart", "imgbb", "imagekit"
    ]

    def test_worker_concurrency_bound(self):
        """Worker count in crosspost_dispatcher must be 11 or 12."""
        self.assertIn(crosspost_dispatcher._MAX_CROSSPOST_WORKERS, (11, 12))


    def test_scratch_test_wallhaven_assertion_bound(self):
        """scratch/test_wallhaven.py must assert 11 workers."""
        test_file = os.path.join(WORKSPACE_DIR, "scratch", "test_wallhaven.py")
        with open(test_file, "r", encoding="utf-8") as f:
            code = f.read()
        self.assertIn("crosspost_dispatcher._MAX_CROSSPOST_WORKERS == 11", code)

    def test_crosspost_dispatcher_header_docstring_contains_all_11(self):
        """Module docstring must enumerate all 11 platforms."""
        doc = crosspost_dispatcher.__doc__
        self.assertIn("all 11 social & image hosting platforms", doc)
        for platform_name in ["Are.na", "Bluesky", "Raindrop.io", "Mastodon", "Pixelfed",
                              "Freeimage.host", "Imghippo", "Wallhaven.cc", "DeviantArt",
                              "ImgBB", "ImageKit.io"]:
            self.assertIn(platform_name, doc)

    def test_crosspost_dispatcher_dispatch_docstring_concurrency_and_timeout(self):
        """dispatch_all_crossposts docstring must specify 11 workers and 60-second budget."""
        fn_doc = crosspost_dispatcher.dispatch_all_crossposts.__doc__
        self.assertIn("11 workers", fn_doc)
        self.assertIn("60-second", fn_doc)
        self.assertIn("_MAX_CROSSPOST_WORKERS = 11", fn_doc)

    def test_backlink_waterfall_header_docstring_contains_all_11(self):
        """backlink_waterfall module docstring must enumerate all 11 platforms."""
        doc = backlink_waterfall.__doc__
        self.assertIn("all 11 connected platforms", doc)
        for platform_name in ["Bluesky", "Mastodon", "Raindrop.io", "Are.na", "Pixelfed",
                              "Wallhaven.cc", "Freeimage.host", "Imghippo", "DeviantArt",
                              "ImgBB", "ImageKit.io"]:
            self.assertIn(platform_name, doc)

    def test_env_example_cleared_of_legacy_and_contains_imagekit(self):
        """.env.example must not contain TUMBLR and must contain all IMAGEKIT keys."""
        env_path = os.path.join(WORKSPACE_DIR, ".env.example")
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Legacy Tumblr removed
        self.assertNotIn("TUMBLR_", content)

        # ImageKit configured
        self.assertIn("IMAGEKIT_PUBLIC_KEY", content)
        self.assertIn("IMAGEKIT_PRIVATE_KEY", content)
        self.assertIn("IMAGEKIT_URL_ENDPOINT", content)
        self.assertIn("IMAGEKIT_ENABLED", content)

    def test_telegram_bot_platforms_meta_matches_all_11(self):
        """telegram_bot.format_crosspost_status_block platforms_meta must contain exactly all 11 platforms."""
        import inspect
        src = inspect.getsource(telegram_bot.format_crosspost_status_block)
        for platform in self.ALL_PLATFORMS:
            self.assertIn(f'"{platform}"', src)


if __name__ == "__main__":
    unittest.main()
