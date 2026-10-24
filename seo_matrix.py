"""
seo_matrix.py — Pinterest Search SEO & Multi-Intent Tag Matrix Engine
=====================================================================
Engineered for Pinterest's visual search ranking algorithm and aligned with
the Multi-Merchant Bridge Page (Amazon Figures + Displate Metal Art + Free 4K Wallpaper).

Features:
1. Multi-Intent Tag Matrix: Balanced sampling across 4 distinct search intents:
   - Intent 1: Wallpaper & Visual Search (Phone, Desktop, 4K, Lockscreens)
   - Intent 2: Room Decor & Wall Art (Posters, Displate metal prints, Otaku decor)
   - Intent 3: Merch & Collectibles (Figures, Statues, Manga, Gifts)
   - Intent 4: Fandom & Character Deep Search (Exact anime + character keywords)
2. SEO Title Optimizer: Front-loads highest-ranking keywords in the first 30 chars
   and strictly enforces Pinterest's 100-character limit.
3. Natural Multi-Intent Description Prepend: Crafts a seamless opening hook in the
   first 150 characters targeting Pinterest's highest-weighted text ranking zone.
4. 100% Offline & Free-Tier Friendly: Zero external API latency, pure Python, <1 MB RAM.
"""

import re
import random
from typing import List, Dict, Optional
from logger import get_logger

logger = get_logger(__name__)

# ── Intent Taxonomy Banks ───────────────────────────────────────────────────

# Intent 1: Wallpaper, Screen & Visual Searchers
WALLPAPER_INTENT_TAGS = [
    "#AnimeWallpaper",
    "#AnimeWallpaper4K",
    "#PhoneWallpaper4K",
    "#AnimeAesthetic",
    "#LockscreenAnime",
    "#AestheticWallpaper",
    "#AnimeDesktopWallpaper",
    "#DarkAnimeAesthetic",
    "#AnimeBackground",
    "#AnimeArt4K",
    "#PhoneLockscreen",
    "#AestheticPhoneWallpaper",
]

# Intent 2: Room Decor, Wall Art & Posters (Displate Synergies)
DECOR_INTENT_TAGS = [
    "#AnimeRoomDecor",
    "#AnimeWallArt",
    "#MetalPosterArt",
    "#AnimePoster",
    "#AnimeRoomAesthetic",
    "#OtakuRoomDecor",
    "#AnimeDisplate",
    "#WallArtPrint",
    "#MangaWallDecor",
    "#AnimeWallPoster",
    "#AnimeCanvas",
    "#AnimeBedroomDecor",
]

# Intent 3: Merch, Figures & Gifts (Amazon Synergies)
MERCH_INTENT_TAGS = [
    "#AnimeMerch",
    "#AnimeFigure",
    "#AnimeCollectibles",
    "#AnimeStatue",
    "#AnimeGiftIdeas",
    "#OtakuMerch",
    "#BuyAnime",
    "#AnimeCollection",
    "#AnimeToys",
    "#AnimeShop",
    "#AnimeGifts",
    "#ActionFigureAnime",
]

# Intent 4: Organic Aesthetic & Art Community
AESTHETIC_INTENT_TAGS = [
    "#AnimeArt",
    "#AnimeAesthetic",
    "#MangaArt",
    "#AnimeArtwork",
    "#OtakuLife",
    "#DigitalArt",
    "#AnimeFanart",
    "#AnimeIllustration",
    "#AnimeDrawing",
    "#AnimeVibes",
    "#JapanArt",
    "#AnimeLovers",
]

DISCLOSURE_TAGS = ["#ad", "#affiliate"]

# ── Dynamic Multi-Intent Tag Generator ─────────────────────────────────────

def _clean_tag(text: str) -> str:
    """Removes special characters and spaces to create a valid hashtag."""
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", text or "")
    return f"#{cleaned}" if cleaned else ""


def generate_fandom_tags(anime_name: str, character_name: str = "") -> List[str]:
    """Generates series and character-specific tags for deep search intent."""
    tags = []
    clean_anime = re.sub(r"[^a-zA-Z0-9\s]", "", anime_name or "").title().replace(" ", "")
    clean_char = re.sub(r"[^a-zA-Z0-9\s]", "", character_name or "").title().replace(" ", "")

    if clean_anime:
        tags.append(f"#{clean_anime}")
        tags.append(f"#{clean_anime}Anime")
        tags.append(f"#{clean_anime}Art")

    if clean_char:
        tags.append(f"#{clean_char}")
        tags.append(f"#{clean_char}FanArt")
        if clean_anime:
            tags.append(f"#{clean_anime}{clean_char}")

    # Fallback/enhancement from existing anime tag banks in hashtag_optimizer if available
    try:
        from hashtag_optimizer import _get_anime_specific_tags
        bank_tags = _get_anime_specific_tags(anime_name)
        if bank_tags:
            for bt in bank_tags:
                if bt not in tags:
                    tags.append(bt)
    except Exception:
        pass

    return tags


def generate_multi_intent_tags(
    anime_name: str,
    character_name: str = "",
    genre: str = "general",
    target_count: int = 13,
    include_disclosure: bool = True,
) -> str:
    """
    Constructs an intent-balanced hashtag cluster.
    
    Recipe:
    - 3 Wallpaper / Visual tags
    - 2-3 Room Decor / Wall Art tags (Displate match)
    - 2 Merch / Figures tags (Amazon match)
    - 2-3 Fandom & Character tags
    - 1-2 Organic Aesthetic tags
    - 1 FTC Disclosure tag (#ad or #affiliate)
    
    Deduplicates and shuffles to ensure unique post fingerprints.
    """
    selected_tags: List[str] = []

    # 1. Wallpaper / Visual Intent (Sample 3)
    k_wall = min(len(WALLPAPER_INTENT_TAGS), 3)
    selected_tags.extend(random.sample(WALLPAPER_INTENT_TAGS, k_wall))

    # 2. Room Decor / Displate Intent (Sample 2-3)
    k_decor = min(len(DECOR_INTENT_TAGS), random.randint(2, 3))
    selected_tags.extend(random.sample(DECOR_INTENT_TAGS, k_decor))

    # 3. Merch / Amazon Figures Intent (Sample 2)
    k_merch = min(len(MERCH_INTENT_TAGS), 2)
    selected_tags.extend(random.sample(MERCH_INTENT_TAGS, k_merch))

    # 4. Fandom & Character Intent (Guarantee primary anime + character, sample 1-2 extra)
    primary_anime_tag = _clean_tag(anime_name.replace(" ", ""))
    if primary_anime_tag:
        selected_tags.append(primary_anime_tag)

    primary_char_tag = ""
    if character_name and character_name.strip():
        primary_char_tag = _clean_tag(character_name.strip().split()[0])
        if primary_char_tag:
            selected_tags.append(primary_char_tag)

    fandom_pool = generate_fandom_tags(anime_name, character_name)
    existing_fandom_lower = {primary_anime_tag.lower(), primary_char_tag.lower()} if primary_char_tag else {primary_anime_tag.lower()}
    extra_fandom = [t for t in fandom_pool if t.lower() not in existing_fandom_lower]
    if extra_fandom:
        k_fan = min(len(extra_fandom), random.randint(1, 2))
        selected_tags.extend(random.sample(extra_fandom, k_fan))

    # 5. Organic Aesthetic (Sample 1-2)
    k_aes = min(len(AESTHETIC_INTENT_TAGS), random.randint(1, 2))
    selected_tags.extend(random.sample(AESTHETIC_INTENT_TAGS, k_aes))

    # 6. FTC Disclosure
    if include_disclosure:
        selected_tags.append(random.choice(DISCLOSURE_TAGS))

    # Deduplicate preserving case-insensitive uniqueness
    seen = set()
    unique_tags = []
    for tag in selected_tags:
        lower_t = tag.lower().strip()
        if lower_t and lower_t not in seen:
            seen.add(lower_t)
            unique_tags.append(tag)

    # Shuffle for non-repetitive bot footprint
    random.shuffle(unique_tags)

    # Cap at target count (keeping disclosure if present)
    has_ad = any(t.lower() in ("#ad", "#affiliate") for t in unique_tags)
    final_tags = unique_tags[:target_count]
    if include_disclosure and has_ad and not any(t.lower() in ("#ad", "#affiliate") for t in final_tags):
        final_tags[-1] = "#ad"

    result = " ".join(final_tags)
    logger.info(f"[SEOMatrix] Generated {len(final_tags)} multi-intent tags for '{anime_name}' (char: '{character_name}')")
    return result


# ── Multi-Intent Title Optimizer ───────────────────────────────────────────

def build_multi_intent_title(
    original_title: str,
    anime_name: str,
    character_name: str = "",
    genre: str = "general",
) -> str:
    """
    Builds a high-ranking Pinterest title front-loaded with primary search terms.
    Pinterest algorithm prioritizes the first 30 characters in search queries.
    
    Format:
      "{Anime} {Character} 4K Wallpaper & Art | {Original Title}"
      or "{Anime} - {Character} | 4K Anime Aesthetic Wallpaper"
      
    Strictly capped at 100 characters.
    """
    clean_anime = (anime_name or "Anime").strip().title()
    clean_char = (character_name or "").strip().title()
    clean_title = (original_title or "").strip()

    # Reject any bracketed placeholders in original_title
    if "<" in clean_title or ">" in clean_title:
        clean_title = ""

    # Build primary anchor (first 30-40 chars)
    if clean_char:
        anchor = f"{clean_anime} {clean_char}"
    else:
        anchor = f"{clean_anime}"

    # Deduplicate: if clean_title starts with or repeats anchor or anime name, strip prefix
    sub_title = clean_title
    for prefix in [anchor, clean_anime, clean_char]:
        if prefix and sub_title.lower().startswith(prefix.lower()):
            sub_title = sub_title[len(prefix):].lstrip(" -:|—")
            break

    # Search modifiers matching our multi-intent pillars
    search_modifiers = [
        "4K Wallpaper & Art",
        "Anime Room Decor & Art",
        "HD Wallpaper & Figure Art",
        "Anime Aesthetic 4K",
        "Fan Art & Poster",
    ]
    modifier = random.choice(search_modifiers)

    if not sub_title or len(sub_title) < 3:
        sub_title = modifier

    # Candidate title structures
    candidates = [
        f"{anchor} {modifier} | {sub_title}",
        f"{anchor} | {sub_title} | 4K Wallpaper",
        f"{anchor} - {sub_title} 🔥 Anime Art",
    ]

    chosen = candidates[0]
    for c in candidates:
        if len(c) <= 100:
            chosen = c
            break

    # Hard cap at 100 chars (safe truncation without breaking words if possible)
    if len(chosen) > 100:
        truncated = chosen[:97].rsplit(" ", 1)[0]
        chosen = f"{truncated}..."

    return chosen


# ── Multi-Intent Description Prepend ───────────────────────────────────────

def build_multi_intent_description(
    base_description: str,
    anime_name: str,
    character_name: str = "",
    link_url: str = "",
) -> str:
    """
    Constructs an optimized pin description.
    
    1. Prepend: Natural multi-intent hook inside the first 150 characters connecting
       4K wallpapers, Displate metal prints, and Amazon figures.
    2. Body: Pin synopsis or AI description + call to action.
    3. Link: Destination bridge URL.
    4. Compliance: FTC disclosure tags.
    """
    clean_anime = (anime_name or "Anime").strip().title()
    clean_char = (character_name or "").strip().title()
    subject = f"{clean_anime} ({clean_char})" if clean_char else clean_anime

    # High-converting opening hooks (<150 characters)
    hooks = [
        f"✨ Free 4K {subject} wallpaper, official figures & metal wall art prints.",
        f"🔥 High-definition 4K {subject} anime wallpaper & room decor collection.",
        f"🎨 Ultra-HD {subject} aesthetic wallpaper, manga posters & collectible merch.",
    ]
    opening_hook = random.choice(hooks)

    # Intent call to action
    cta = (
        f"\n\nLooking for official {clean_anime} figures, metal poster prints, or free 4K wallpaper downloads? "
        f"Explore the full anime showcase below!"
    )

    clean_base = re.sub(r"#\w+", "", base_description or "").strip()
    clean_base = clean_base.replace("##LINK_PLACEHOLDER##", "").strip()

    parts = [opening_hook]
    if clean_base:
        parts.append(clean_base)
    parts.append(cta)

    if link_url:
        parts.append(f"\n👉 {link_url}")

    return "\n\n".join(parts)
