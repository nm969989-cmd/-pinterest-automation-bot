"""
Full system health check for the Anime Pinterest Bot.
Tests all key modules without making real API calls or network requests.
"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')

results = []

def check(name, fn):
    try:
        fn()
        results.append(("PASS", name))
        print(f"  [PASS] {name}")
    except Exception as e:
        results.append(("FAIL", name, str(e)))
        print(f"  [FAIL] {name}: {e}")

print("=" * 55)
print("  ANIME PINTEREST BOT — FULL SYSTEM HEALTH CHECK")
print("=" * 55)

# ── 1. Logger ─────────────────────────────────────────────
print("\n[1] Logger")
def test_logger():
    from logger import get_logger
    log = get_logger("test")
    log.info("Logger working")
check("get_logger() returns logger", test_logger)

# ── 2. Config ─────────────────────────────────────────────
print("\n[2] Config")
def test_config_keys():
    import config
    required = ["AMAZON_AFFILIATE_TAG", "MAKE_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN",
                "OPENROUTER_API_KEY", "MAX_POSTS_PER_DAY", "DRY_RUN"]
    for k in required:
        assert hasattr(config, k), f"Missing config: {k}"
check("All required config keys present", test_config_keys)

def test_config_values():
    import config
    assert config.AMAZON_AFFILIATE_TAG, "AMAZON_AFFILIATE_TAG is empty"
    assert config.MAKE_WEBHOOK_URL, "MAKE_WEBHOOK_URL is empty"
    assert config.MAX_POSTS_PER_DAY > 0, "MAX_POSTS_PER_DAY must be > 0"
check("Critical config values are set", test_config_values)

# ── 3. Database ────────────────────────────────────────────
print("\n[3] Database")
def test_db_init():
    from database import init_db, _get_conn
    with _get_conn() as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = [t[0] for t in tables]
    for t in ["pin_queue", "uploaded_files", "processed_posts"]:
        assert t in names, f"Missing table: {t}"
check("All 3 tables exist", test_db_init)

def test_db_indexes():
    from database import _get_conn
    with _get_conn() as conn:
        indexes = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()]
    assert "idx_queue_sched" in indexes, "Missing idx_queue_sched"
    assert "idx_uploads_date" in indexes, "Missing idx_uploads_date"
check("Performance indexes exist", test_db_indexes)

def test_db_queue_functions():
    from database import get_queue_counts, get_queue_detail, clear_pin_queue
    counts = get_queue_counts()
    assert "new" in counts and "backlog" in counts and "total" in counts
    detail = get_queue_detail()
    assert isinstance(detail, list)
check("Queue functions (get_queue_counts, get_queue_detail)", test_db_queue_functions)

def test_db_pop():
    from database import pop_next_pin_for_immediate_post
    pin = pop_next_pin_for_immediate_post()
    assert pin is None or isinstance(pin, dict)
check("pop_next_pin_for_immediate_post() returns dict or None", test_db_pop)

# ── 4. Board Router ────────────────────────────────────────
print("\n[4] Board Router")
def test_board_known():
    from board_router import get_board_for_anime
    genre, _ = get_board_for_anime("Demon Slayer")
    assert genre == "shonen", f"Expected shonen, got {genre}"
check("Demon Slayer -> shonen", test_board_known)

def test_board_isekai():
    from board_router import get_board_for_anime
    genre, _ = get_board_for_anime("Re:Zero")
    assert genre == "isekai", f"Expected isekai, got {genre}"
check("Re:Zero -> isekai", test_board_isekai)

def test_board_unknown():
    from board_router import get_board_for_anime
    genre, board_id = get_board_for_anime("Unknown Anime XYZABC")
    assert genre == "general"
check("Unknown anime -> general (fallback)", test_board_unknown)

# ── 5. Hashtag Optimizer ───────────────────────────────────
print("\n[5] Hashtag Optimizer")
def test_hashtag_count():
    from hashtag_optimizer import optimize_hashtags
    tags = optimize_hashtags("Demon Slayer", "shonen", "Tanjiro", "anime poster")
    tag_list = tags.split()
    assert 10 <= len(tag_list) <= 15, f"Expected 10-15 tags, got {len(tag_list)}"
check("Generates 10-15 tags for Demon Slayer", test_hashtag_count)

def test_hashtag_compliance():
    from hashtag_optimizer import optimize_hashtags
    tags = optimize_hashtags("Naruto", "shonen", "Naruto", "anime hoodie")
    assert "#ad" in tags, "Missing #ad tag"
    assert "#affiliate" in tags, "Missing #affiliate tag"
check("#ad and #affiliate always present", test_hashtag_compliance)

def test_hashtag_replace():
    from hashtag_optimizer import replace_hashtags_in_description
    desc = "Great anime art!\n\n#anime #naruto #old"
    result = replace_hashtags_in_description(desc, "#DemonSlayer #ad")
    assert "#DemonSlayer" in result, "Optimized tags not injected"
check("replace_hashtags_in_description() works", test_hashtag_replace)

# ── 6. Scheduler Jitter ────────────────────────────────────
print("\n[6] Scheduler Jitter")
def test_jitter_count():
    from scheduler import _get_daily_jitter, _BASE_POST_TIMES_UTC
    jitters = _get_daily_jitter()
    assert len(jitters) == len(_BASE_POST_TIMES_UTC), "Jitter count mismatch"
check("Jitter count matches slot count (3)", test_jitter_count)

def test_jitter_stable():
    from scheduler import _get_daily_jitter
    j1 = _get_daily_jitter()
    j2 = _get_daily_jitter()
    assert j1 == j2, "Jitter changed between calls (should be stable per day)"
check("Jitter is stable within same day", test_jitter_stable)

def test_jitter_range():
    from scheduler import _get_daily_jitter, _JITTER_MAX_MINUTES
    for offset in _get_daily_jitter():
        assert -_JITTER_MAX_MINUTES <= offset <= _JITTER_MAX_MINUTES
check(f"All jitter offsets within +/-{20}min range", test_jitter_range)

def test_jittered_times():
    from scheduler import _get_jittered_times_utc, _BASE_POST_TIMES_UTC
    times = _get_jittered_times_utc()
    assert len(times) == len(_BASE_POST_TIMES_UTC)
    for (h, m) in times:
        assert 0 <= h <= 23 and 0 <= m <= 59
check("Jittered UTC times are valid (0-23h, 0-59m)", test_jittered_times)

# ── 7. Image Processor ────────────────────────────────────
print("\n[7] Image Processor")
def test_image_no_watermark():
    import inspect, image_processor
    src = inspect.getsource(image_processor)
    assert "Source: @" not in src, "Competitor watermark code still present!"
    assert "ImageDraw" not in src, "ImageDraw still imported (watermark remnant)"
check("Competitor watermark fully removed", test_image_no_watermark)

def test_image_constants():
    from image_processor import PINTEREST_WIDTH, PINTEREST_HEIGHT
    assert PINTEREST_WIDTH == 1000
    assert PINTEREST_HEIGHT == 1500
check("Pinterest dimensions: 1000x1500 (2:3 ratio)", test_image_constants)

# ── 8. CTA Rotation ───────────────────────────────────────
print("\n[8] CTA Rotation (ai_caption)")
def test_cta_bank():
    from ai_caption import _CTA_TEMPLATES
    assert len(_CTA_TEMPLATES) >= 6, "Need at least 6 CTA templates"
    for t in _CTA_TEMPLATES:
        assert "##LINK_PLACEHOLDER##" in t, f"CTA missing placeholder: {t}"
check(f"CTA bank has {6}+ templates, all with link placeholder", test_cta_bank)

def test_cta_inject():
    from ai_caption import _inject_link
    desc = _inject_link("Amazing artwork!", "TestChannel")
    assert "##LINK_PLACEHOLDER##" in desc, "Placeholder not in description"
check("_inject_link() injects ##LINK_PLACEHOLDER##", test_cta_inject)

# ── 9. Amazon Search ─────────────────────────────────────
print("\n[9] Amazon Search")
def test_amazon_fallback():
    from amazon_search import _build_search_link
    from config import AMAZON_AFFILIATE_TAG
    link = _build_search_link("Demon Slayer anime poster")
    assert AMAZON_AFFILIATE_TAG in link
    assert "amazon.in" in link
check("Fallback search link contains affiliate tag", test_amazon_fallback)

def test_amazon_deep_link():
    from amazon_search import _build_deep_link
    from config import AMAZON_AFFILIATE_TAG
    link = _build_deep_link("B08XYZ1234")
    assert "B08XYZ1234" in link
    assert "/dp/" in link
    assert AMAZON_AFFILIATE_TAG in link
check("Deep link /dp/ASIN contains tag", test_amazon_deep_link)

def test_product_types_no_hoodie():
    from amazon_search import _PRODUCT_TYPES
    assert "anime hoodie" not in _PRODUCT_TYPES, "'anime hoodie' should not be in _PRODUCT_TYPES (causes generic clothes)"
    for pt in _PRODUCT_TYPES:
        assert any(k in pt for k in ("poster", "figure", "art", "plush", "merchandise", "sticker", "keychain", "scroll"))
check("Product types contain only anime artwork/collectibles (no hoodies)", test_product_types_no_hoodie)

def test_character_sanitization():
    from amazon_search import clean_character_name
    assert clean_character_name("Seductive") == "", "Adjective 'Seductive' must not be accepted as character name"
    assert clean_character_name("Cute") == ""
    assert clean_character_name("Tanjiro") == "Tanjiro"
    assert clean_character_name("Luffy") == "Luffy"
    assert clean_character_name("Gojo") == "Gojo"
check("Character name cleaner filters adjectives and allows valid anime characters", test_character_sanitization)

def test_anime_name_sanitization():
    from amazon_search import _sanitize_anime_name
    assert "Demon Slayer" in _sanitize_anime_name("Demon Slayer: Kimetsu no Yaiba")
    # Compound title with fluff words should be cleaned
    cleaned = _sanitize_anime_name("Seductive Original Art / Isekai Demon")
    assert "/" not in cleaned and "Seductive" not in cleaned and "Original Art" not in cleaned
    assert "Isekai Demon" in cleaned or "Anime" in cleaned
check("Anime name sanitizer cleans compound names, slashes, and fluff words", test_anime_name_sanitization)

def test_relevance_checker():
    from amazon_search import _is_product_relevant
    assert not _is_product_relevant("NOBERO Men's Fleece Hooded Hoodie Yellow", "Isekai Demon")
    assert not _is_product_relevant("Men's Plain Cotton T-Shirt", "Demon Slayer")
    assert _is_product_relevant("Demon Slayer Tanjiro Action Figure Anime Collectible", "Demon Slayer")
    assert _is_product_relevant("Anime Aesthetic Wall Art Poster HD Print", "Anime")
check("Amazon product relevance filter rejects generic clothes and accepts anime merch", test_relevance_checker)

# ── Results Summary ───────────────────────────────────────
print()
print("=" * 55)
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
print(f"  RESULTS: {passed} PASSED  |  {failed} FAILED  |  {len(results)} TOTAL")
print("=" * 55)
if failed:
    print("\nFailed checks:")
    for r in results:
        if r[0] == "FAIL":
            print(f"  - {r[1]}: {r[2]}")
else:
    print("  All systems healthy. Bot is ready to run.")
print()
