"""
backlink_waterfall.py — Backlink SEO Waterfall & Dual-Anchor Syndication Engine
================================================================================
Boosts Google and Bing search indexing and domain authority for Pinterest pins
by syndicating high-authority backlinks across all 11 connected platforms:
  1. Bluesky (DA 90+)
  2. Mastodon (DA 85+)
  3. Raindrop.io (DA 80+)
  4. Are.na (DA 75+)
  5. Pixelfed (DA 75+)
  6. Wallhaven.cc (DA 75+)
  7. Freeimage.host (DA 65+)
  8. Imghippo (DA 60+)
  9. DeviantArt (DA 90+)
  10. ImgBB (DA 70+)
  11. ImageKit.io (Global CDN)

Dual-Anchor Architecture:
  - Anchor 1 (Authority / Search Indexing): Backlinks to the live Pinterest Pin or
    Pinterest Profile (https://in.pinterest.com/muthelyrics/).
  - Anchor 2 (Monetization / Conversion): Backlinks to the Multi-Merchant Bridge Page
    (/p/<code>) for Free 4K Wallpapers, Displate metal art, and Amazon figures.
"""

import os
import re
from typing import Dict, Any, Optional
from logger import get_logger

logger = get_logger(__name__)

# Fallback profile URL if direct pin URL is still processing
_DEFAULT_PINTEREST_PROFILE = "https://in.pinterest.com/muthelyrics/"


def resolve_pinterest_url(pin_url: str = "") -> str:
    """
    Returns the specific pin URL if available, or falls back to the configured
    Pinterest profile URL.
    """
    if pin_url and str(pin_url).strip().startswith("http"):
        return str(pin_url).strip()
    try:
        from config import PINTEREST_PROFILE_URL
        if PINTEREST_PROFILE_URL and str(PINTEREST_PROFILE_URL).strip().startswith("http"):
            return str(PINTEREST_PROFILE_URL).strip()
    except Exception:
        pass
    return _DEFAULT_PINTEREST_PROFILE


def format_waterfall_caption(
    platform: str,
    title: str,
    description: str,
    bridge_url: str,
    pin_url: str = "",
    anime_name: str = "",
    character_name: str = "",
    is_stealth: bool = False,
) -> str:
    """
    Constructs platform-native copy containing dual backlinks (Pinterest + Bridge Page)
    tailored strictly to character budgets and platform etiquette.
    """
    plat = platform.lower().strip()
    resolved_pin_url = resolve_pinterest_url(pin_url)
    resolved_bridge = bridge_url.strip() if bridge_url else ""

    # Fetch platform-specific tags
    try:
        from hashtag_optimizer import get_platform_tags
        tags = get_platform_tags(anime_name=anime_name, platform=plat, character_name=character_name)
    except Exception:
        tags = ["#AnimeArt", "#AnimeWallpaper4K", "#AnimeAesthetic"]
    tags_str = " ".join(tags)

    # Clean text body (remove existing hashtag blocks)
    body_lines = []
    for line in (description or "").split("\n"):
        tokens = line.strip().split()
        if tokens and sum(1 for t in tokens if t.startswith("#")) >= len(tokens) * 0.5:
            continue
        body_lines.append(line)
    clean_body = "\n".join(body_lines).strip()
    if not clean_body:
        clean_body = title.strip()

    # ── 1. Bluesky (Strict 300 character budget) ─────────────────────────────
    if plat in ("bluesky", "bsky"):
        clean_title = title.split(" - ")[0].split(" | ")[0].strip()[:70]
        
        # Build Dual-Anchor block
        link_lines = []
        if resolved_pin_url:
            link_lines.append(f"📌 Pin: {resolved_pin_url}")
        if resolved_bridge and not is_stealth:
            link_lines.append(f"🖼️ 4K Wall: {resolved_bridge}")
        links_block = "\n".join(link_lines)

        caption = f"{clean_title}\n\n{links_block}\n\n{tags_str}".strip()
        
        # If over 300 chars, drop tags first, then truncate title to keep links intact
        if len(caption) > 300:
            caption = f"{clean_title}\n\n{links_block}".strip()
        if len(caption) > 300:
            excess = len(caption) - 296
            clean_title = clean_title[:-excess].rstrip() + "..."
            caption = f"{clean_title}\n\n{links_block}".strip()

        return caption[:300]

    # ── 2. Mastodon (500 character budget, polite community tone) ─────────────
    elif plat == "mastodon":
        parts = [title.strip()]
        snippet = clean_body[:180].strip()
        if snippet and snippet != title.strip():
            parts.append(snippet)
        
        link_block = []
        if resolved_pin_url:
            link_block.append(f"📌 Save on Pinterest: {resolved_pin_url}")
        if resolved_bridge and not is_stealth:
            link_block.append(f"🖼️ Free 4K Wallpaper & Merch: {resolved_bridge}")
        if link_block:
            parts.append("\n".join(link_block))
        
        if tags_str:
            parts.append(tags_str)

        return "\n\n".join(parts)[:490]

    # ── 3. Pixelfed (Visual Gallery & Photo Feed) ─────────────────────────────
    elif plat == "pixelfed":
        parts = [title.strip()]
        if clean_body and clean_body != title.strip():
            parts.append(clean_body[:220].strip())
        
        link_block = []
        if resolved_pin_url:
            link_block.append(f"📌 Pinterest Board: {resolved_pin_url}")
        if resolved_bridge and not is_stealth:
            link_block.append(f"🖼️ HD Downloads & Art Prints: {resolved_bridge}")
        if link_block:
            parts.append("\n".join(link_block))
            
        if tags_str:
            parts.append(tags_str)

        return "\n\n".join(parts)

    # ── 4. Are.na (Creative Research & Source Attribution) ────────────────────
    elif plat == "arena":
        arena_parts = [title.strip()]
        if clean_body and clean_body != title.strip():
            arena_parts.append(clean_body[:200].strip())
        if resolved_pin_url:
            arena_parts.append(f"Source Pin: {resolved_pin_url}")
        if resolved_bridge and not is_stealth:
            arena_parts.append(f"Showcase & Prints: {resolved_bridge}")
        return "\n\n".join(arena_parts)

    elif plat in ("deviantart", "da"):
        da_parts = [title.strip()]
        if clean_body and clean_body != title.strip():
            da_parts.append(clean_body)
        if resolved_pin_url:
            da_parts.append(f"📌 View on Pinterest: {resolved_pin_url}")
        if resolved_bridge and not is_stealth:
            da_parts.append(f"🖼️ Download 4K HD Wallpaper & Merch: {resolved_bridge}")
        if tags_str:
            da_parts.append(tags_str)
        return "\n\n".join(da_parts)

    # ── 5. General Fallback ───────────────────────────────────────────────────
    return clean_body


def record_waterfall_backlinks(
    filename: str,
    anime_name: str,
    crosspost_results: Dict[str, Any],
    bridge_url: str = "",
    pin_url: str = "",
) -> int:
    """
    Inspects cross-posting results and records all generated backlinks in SQLite.
    Returns the count of backlinks successfully logged.
    """
    try:
        from database import record_backlink
    except ImportError:
        return 0

    resolved_pin = resolve_pinterest_url(pin_url)
    logged = 0

    platform_keys = [
        "bluesky", "mastodon", "raindrop", "arena",
        "pixelfed", "wallhaven", "freeimage", "imghippo", "deviantart", "imgbb", "imagekit"
    ]

    for plat in platform_keys:
        if crosspost_results.get(f"{plat}_ok"):
            # Record Pinterest authority anchor
            if resolved_pin:
                record_backlink(filename, anime_name, plat, resolved_pin, anchor_type="pinterest")
                logged += 1
            # Record Bridge Page conversion anchor
            if bridge_url:
                record_backlink(filename, anime_name, plat, bridge_url, anchor_type="bridge")
                logged += 1

    if logged > 0:
        logger.info(f"[BacklinkWaterfall] Recorded {logged} external backlinks for '{filename}' ({anime_name})")

    return logged
