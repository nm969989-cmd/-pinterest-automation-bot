import os
import sys
import pytest
from PIL import Image, ImageDraw

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from image_processor import is_promotional_caption, is_marketing_or_ad_image
from ai_caption import generate_pin_content, _parse_ai_response


def test_is_promotional_caption_detects_ads():
    ad_captions = [
        "Advertise on Telegram channels - Deal directly with channel owners - Escrow protected",
        "Skip the ad review queue? Go live in 60 seconds",
        "Ads budget statistics settings - side income channel promo",
        "Join our VIP signals channel for daily crypto profit! t.me/+fakeinvite",
        "Monetize your Telegram audience today with our official sponsor",
        "DM for promo or paid sponsorship @ad_manager",
    ]
    for cap in ad_captions:
        is_promo, reason = is_promotional_caption(cap)
        assert is_promo is True, f"Failed to detect promo in: {cap}"
        assert len(reason) > 0


def test_is_promotional_caption_permits_anime():
    clean_captions = [
        "Levi Ackerman preparing for battle against the Beast Titan #AttackOnTitan",
        "Gojo Satoru domain expansion infinite void - Jujutsu Kaisen 4K wallpaper",
        "Tanjiro and Nezuko aesthetic scene from Demon Slayer swordsmith village",
        "Solo Leveling Sung Jinwoo shadow monarch dark aesthetic lockscreen",
        "",
        None,
    ]
    for cap in clean_captions:
        is_promo, reason = is_promotional_caption(cap)
        assert is_promo is False, f"False positive on clean caption: {cap}"


def test_is_marketing_or_ad_image_heuristics(tmp_path):
    # 1. Authentic OLED anime artwork (Dark background + colored subject) -> Must be ALLOWED
    clean_img_path = str(tmp_path / "oled_anime.jpg")
    img = Image.new("RGB", (1080, 1920), color=(10, 10, 15))
    draw = ImageDraw.Draw(img)
    for r in range(250, 0, -10):
        draw.ellipse([540 - r, 960 - r, 540 + r, 960 + r], fill=(r, 120, 255 - r // 2))
    img.save(clean_img_path)

    is_ad, reason = is_marketing_or_ad_image(clean_img_path, "Clean anime art")
    assert is_ad is False, f"Clean anime image was falsely flagged: {reason}"

    # 2. Text-heavy flyer / banner -> Must be BLOCKED
    ad_banner_path = str(tmp_path / "ad_banner.jpg")
    ad_img = Image.new("RGB", (800, 800), color=(240, 240, 245))
    ad_draw = ImageDraw.Draw(ad_img)
    # Simulate multiple rectangular text cards / banners
    for y in range(40, 700, 60):
        ad_draw.rectangle([50, y, 750, y + 35], fill=(30, 30, 40))
    ad_img.save(ad_banner_path)

    is_ad, reason = is_marketing_or_ad_image(ad_banner_path, "")
    assert is_ad is True, "Marketing banner was not flagged!"
    assert "text" in reason.lower() or "ui" in reason.lower() or "flat" in reason.lower()


def test_ai_parse_rejection():
    # Test that AI responses with is_ad_or_marketing are rejected
    ad_response = '{"is_ad_or_marketing": true, "rejection_reason": "Telegram ad flyer with pricing"}'
    parsed = _parse_ai_response(ad_response, "Anime_Channel")
    assert parsed[0] == "REJECTED_AD"

    # Test that promotional words in AI-generated title are caught
    promo_title_response = '{"anime_name": "Anime", "title": "Advertise On Telegram Channels Cheap", "description": "Buy ads now"}'
    parsed2 = _parse_ai_response(promo_title_response, "Anime_Channel")
    assert parsed2[0] == "REJECTED_AD"


def test_generate_pin_content_blocks_promo_captions():
    res = generate_pin_content("Advertise on Telegram channels with huge discount!", "Test_Channel")
    assert res[0] == "REJECTED_AD"
