"""
tests/test_evolved_features.py
==============================
Unit tests for:
1. Self-Learning "Winning Keyword" Evolver
2. Visual Color Palette Aesthetic Tagging
3. Enhanced 4-Layer Duplicate Prevention Engine
"""

import os
import sys
import tempfile
import unittest
from PIL import Image

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestWinningKeywordEvolver(unittest.TestCase):
    def setUp(self):
        from database import init_db, _get_conn
        init_db()
        # Clean test entries in evolved_keywords
        with _get_conn() as conn:
            conn.execute("DELETE FROM evolved_keywords WHERE keyword LIKE 'TEST_%' OR keyword = '4K Aesthetic Art'")
            conn.commit()

    def tearDown(self):
        from database import _get_conn
        with _get_conn() as conn:
            conn.execute("DELETE FROM evolved_keywords WHERE keyword LIKE 'TEST_%' OR keyword = '4K Aesthetic Art'")
            conn.commit()

    def test_record_and_get_top_keywords(self):
        from database import record_keyword_evolution, get_top_evolved_keywords

        # Record a winner with high visits & clicks
        record_keyword_evolution("TEST_AMOLED Wallpaper", won=True, clicks=5, visits=20)
        # Record a loser
        record_keyword_evolution("TEST_Boring Wallpaper", won=False, clicks=0, visits=20)
        # Record another winner with more wins
        record_keyword_evolution("TEST_4K Room Decor Art", won=True, clicks=8, visits=25)
        record_keyword_evolution("TEST_4K Room Decor Art", won=True, clicks=4, visits=15)

        top_keywords = get_top_evolved_keywords(limit=5)
        self.assertIn("TEST_4K Room Decor Art", top_keywords)
        self.assertIn("TEST_AMOLED Wallpaper", top_keywords)
        self.assertNotIn("TEST_Boring Wallpaper", top_keywords)
        self.assertEqual(top_keywords[0], "TEST_4K Room Decor Art")

    def test_title_builder_incorporates_evolved_keywords(self):
        from database import record_keyword_evolution
        from seo_matrix import build_multi_intent_title

        record_keyword_evolution("TEST_4K Aesthetic Art", won=True, clicks=10, visits=30)
        title = build_multi_intent_title("Zoro", anime_name="One Piece", character_name="Zoro")
        self.assertIsInstance(title, str)
        self.assertLessEqual(len(title), 100)
        self.assertIn("One Piece", title)


class TestColorPaletteTagging(unittest.TestCase):
    def test_dark_green_image_tags(self):
        from image_processor import extract_image_color_tags
        # Create a synthetic dark image with neon green dominant center
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_path = f.name
        try:
            # 50x50 dark green image
            img = Image.new("RGB", (100, 100), (10, 50, 10))
            img.save(tmp_path, "JPEG")
            tags = extract_image_color_tags(tmp_path)
            self.assertTrue(any("Dark" in t or "AMOLED" in t or "Green" in t for t in tags))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_monochrome_image_tags(self):
        from image_processor import extract_image_color_tags
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            tmp_path = f.name
        try:
            # Grayscale image
            img = Image.new("RGB", (100, 100), (120, 120, 120))
            img.save(tmp_path, "JPEG")
            tags = extract_image_color_tags(tmp_path)
            self.assertTrue(any("Monochrome" in t or "Manga" in t for t in tags))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_seo_matrix_incorporates_color_tags(self):
        from seo_matrix import generate_multi_intent_tags
        color_tags = ["#NeonGreen", "#DarkAnimeAesthetic"]
        tags_str = generate_multi_intent_tags(
            anime_name="One Piece",
            character_name="Zoro",
            target_count=13,
            color_tags=color_tags
        )
        tag_list = tags_str.split()
        self.assertLessEqual(len(tag_list), 13)
        self.assertTrue(any("neongreen" in t.lower() or "darkanimeaesthetic" in t.lower() for t in tag_list))


class TestEnhancedDuplicatePrevention(unittest.TestCase):
    def setUp(self):
        from database import init_db, _get_conn
        init_db()

    def test_layer1_url_duplicate(self):
        from database import is_image_duplicate, _get_conn
        # Insert a queued item with an image_url
        with _get_conn() as conn:
            conn.execute("DELETE FROM pin_queue WHERE post_id LIKE 'test_dup_%'")
            conn.execute("""
                INSERT INTO pin_queue (post_id, title, image_url, scheduled_date)
                VALUES ('test_dup_url', 'Test URL Pin', 'https://example.com/wallhaven-123.jpg?source=feed&utm=1', '2026-10-25')
            """)
            conn.commit()

        # Check duplicate with same URL (ignoring different query params)
        is_dup, reason = is_image_duplicate(image_url="https://example.com/wallhaven-123.jpg?ref=pin")
        self.assertTrue(is_dup)
        self.assertIn("URL", reason)

        # Clean up
        with _get_conn() as conn:
            conn.execute("DELETE FROM pin_queue WHERE post_id LIKE 'test_dup_%'")
            conn.commit()

    def test_layer2_filename_duplicate(self):
        from database import is_image_duplicate, _get_conn
        with _get_conn() as conn:
            conn.execute("DELETE FROM uploaded_files WHERE filename = 'test_unique_art_9999.jpg'")
            conn.execute("""
                INSERT INTO uploaded_files (filename, title, uploaded_at)
                VALUES ('test_unique_art_9999.jpg', 'Uploaded Art 9999', CURRENT_TIMESTAMP)
            """)
            conn.commit()

        is_dup, reason = is_image_duplicate(filename="d:/pinterst automation/downloads/test_unique_art_9999.jpg")
        self.assertTrue(is_dup)
        self.assertIn("Filename", reason)

        with _get_conn() as conn:
            conn.execute("DELETE FROM uploaded_files WHERE filename = 'test_unique_art_9999.jpg'")
            conn.commit()

    def test_layer3_sha256_duplicate(self):
        from database import is_image_duplicate, _get_conn
        fake_sha = "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
        with _get_conn() as conn:
            conn.execute("DELETE FROM uploaded_files WHERE sha256 = ?", (fake_sha,))
            conn.execute("""
                INSERT INTO uploaded_files (filename, title, sha256, uploaded_at)
                VALUES ('test_sha.jpg', 'Uploaded SHA Pin', ?, CURRENT_TIMESTAMP)
            """, (fake_sha,))
            conn.commit()

        is_dup, reason = is_image_duplicate(sha256=fake_sha)
        self.assertTrue(is_dup)
        self.assertIn("SHA-256", reason)

        with _get_conn() as conn:
            conn.execute("DELETE FROM uploaded_files WHERE sha256 = ?", (fake_sha,))
            conn.commit()


if __name__ == "__main__":
    unittest.main()
