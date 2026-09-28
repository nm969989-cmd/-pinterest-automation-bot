"""
keyword_seo.py — Pinterest Keyword SEO Injector
================================================
Pinterest is fundamentally a search engine. Pins that contain the exact
keywords people are searching for appear in search results and get organic
reach for months or years after posting.

This module:
1. Queries Pinterest's live autocomplete/typeahead API (no API key needed)
   to fetch real trending search terms for any anime name.
2. Caches results in SQLite (bot_state.db) for 24 hours to avoid rate-limiting.
3. Builds an SEO-optimized title and injects keywords into the description.
4. Tracks which keywords are used per anime to build a learning keyword bank.

Architecture (from graphify query):
  main.py → generate_pin_content() → generate_amazon_link()
                ↓
  This module wraps the title/description with SEO keywords BEFORE
  the hashtag_optimizer runs, so everything is layered cleanly.
"""

import re
import time
import json
import random
import requests
from logger import get_logger

logger = get_logger(__name__)

# Pinterest typeahead endpoint (public, no auth required)
_PINTEREST_AUTOCOMPLETE_URL = "https://www.pinterest.com/resource/SearchBarResource/get/"
_PINTEREST_TYPEAHEAD_URL    = "https://www.pinterest.com/resource/TypeAheadResource/get/"

# Cache TTL — refresh keywords every 24 hours per anime
_CACHE_TTL_HOURS = 24

# Max keywords to inject per pin (keep natural, not spammy)
_MAX_INJECT_KEYWORDS = 4

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/javascript, */*, q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.pinterest.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# ── High-value Pinterest anime search keyword templates ───────────────────────
# These are curated based on real Pinterest search volume patterns for anime content.
# Used as fallback when live autocomplete fails.
_KEYWORD_TEMPLATES = [
    "{anime} wallpaper 4k",
    "{anime} anime aesthetic",
    "{anime} fan art",
    "{anime} poster",
    "{anime} pfp",
    "{anime} cute",
    "{anime} dark aesthetic",
    "{anime} art",
    "{anime} characters",
    "{anime} {character} wallpaper",
    "{anime} {character} fan art",
    "{anime} {character} aesthetic",
    "{anime} merch",
    "{anime} gifts",
]

# Title SEO templates — high-click patterns on Pinterest
_TITLE_TEMPLATES = [
    "{title} | {anime} Anime Wallpaper 4K",
    "{anime} - {title} 🔥 Anime Aesthetic",
    "{title} • {anime} Fan Art Poster",
    "{anime} Anime | {title} | HD Wallpaper",
    "{title} | {anime} | Anime Art Aesthetic",
]


# ── SQLite keyword cache ───────────────────────────────────────────────────────

def _get_cached_keywords(anime_name: str) -> list[str] | None:
    """Returns cached keywords for this anime if not expired, else None."""
    try:
        from database import _get_conn
        with _get_conn() as conn:
            row = conn.execute("""
                SELECT keywords_json, fetched_at FROM seo_keyword_cache
                WHERE anime_name = ? AND
                      datetime(fetched_at, '+24 hours') > datetime('now')
            """, (anime_name.lower().strip(),)).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def _cache_keywords(anime_name: str, keywords: list[str]) -> None:
    """Store keywords in the cache table."""
    try:
        from database import _get_conn
        with _get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO seo_keyword_cache
                    (anime_name, keywords_json, fetched_at)
                VALUES (?, ?, datetime('now'))
            """, (anime_name.lower().strip(), json.dumps(keywords)))
            conn.commit()
    except Exception as e:
        logger.debug(f"[KeywordSEO] Cache write failed (non-critical): {e}")


def _ensure_cache_table() -> None:
    """Create the seo_keyword_cache table if it doesn't exist yet."""
    try:
        from database import _get_conn
        with _get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seo_keyword_cache (
                    anime_name   TEXT PRIMARY KEY,
                    keywords_json TEXT,
                    fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    except Exception as e:
        logger.debug(f"[KeywordSEO] Table creation failed (non-critical): {e}")


# ── Live Pinterest autocomplete fetch ─────────────────────────────────────────

def fetch_pinterest_keywords(anime_name: str, max_results: int = 10) -> list[str]:
    """
    Queries Pinterest's typeahead API to get live trending search terms
    for the given anime. Returns a list of keyword strings.

    Falls back to template-based keywords if the API is unavailable.
    Results are cached for 24 hours.
    """
    _ensure_cache_table()

    # Check cache first
    cached = _get_cached_keywords(anime_name)
    if cached:
        logger.info(f"[KeywordSEO] Using cached keywords for '{anime_name}': {cached[:3]}...")
        return cached

    keywords = []

    # Try Pinterest TypeAhead API (no key required, just session headers)
    queries_to_try = [
        anime_name,
        f"{anime_name} anime",
        f"{anime_name} wallpaper",
    ]

    for query in queries_to_try:
        try:
            params = {
                "source_url": "/search/pins/",
                "data": json.dumps({
                    "options": {
                        "query": query,
                        "scope": "pins",
                        "fields": "term",
                    },
                    "context": {}
                }),
            }
            resp = requests.get(
                _PINTEREST_TYPEAHEAD_URL,
                params=params,
                headers=_HEADERS,
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Navigate the response structure
                results = (
                    data.get("resource_response", {})
                        .get("data", {})
                        .get("results", [])
                )
                for r in results:
                    term = r.get("term") or r.get("display") or ""
                    if term and len(term) > 3:
                        keywords.append(term.strip())
                if keywords:
                    break  # Got results, stop trying queries
        except Exception as e:
            logger.debug(f"[KeywordSEO] TypeAhead API attempt failed: {e}")
        time.sleep(0.5)  # Be polite to Pinterest's servers

    # Deduplicate and limit
    seen = set()
    unique_keywords = []
    for kw in keywords:
        kl = kw.lower()
        if kl not in seen:
            seen.add(kl)
            unique_keywords.append(kw)
        if len(unique_keywords) >= max_results:
            break

    # Fallback: generate template-based keywords if live fetch got nothing
    if not unique_keywords:
        logger.info(f"[KeywordSEO] Live fetch returned nothing — using curated templates for '{anime_name}'")
        unique_keywords = _build_template_keywords(anime_name)

    # Cache and return
    _cache_keywords(anime_name, unique_keywords)
    logger.info(f"[KeywordSEO] Keywords for '{anime_name}': {unique_keywords[:5]}")
    return unique_keywords


def _build_template_keywords(anime_name: str, character_name: str = "") -> list[str]:
    """Generate curated high-volume keyword phrases from templates."""
    keywords = []
    clean = anime_name.strip().title()
    char  = character_name.strip().title() if character_name else ""
    for template in _KEYWORD_TEMPLATES:
        try:
            kw = template.format(anime=clean, character=char or clean)
            keywords.append(kw)
        except KeyError:
            keywords.append(template.format(anime=clean))
    return keywords[:10]


# ── SEO Title Builder ──────────────────────────────────────────────────────────

def build_seo_title(
    original_title: str,
    anime_name: str,
    character_name: str = "",
    genre: str = "general",
) -> str:
    """
    Takes the AI-generated pin title and wraps it in an SEO-optimized
    format that Pinterest's search algorithm prioritizes.

    Pinterest title SEO rules:
    - First 30 characters are weighted most heavily in search ranking
    - Include anime name + content type (wallpaper / aesthetic / fan art)
    - Avoid ALL CAPS — Pinterest's algorithm penalizes it
    - Include the character name if known (people search by character)

    Returns an enhanced title capped at 100 chars (Pinterest's limit).
    """
    clean_anime  = anime_name.strip().title() if anime_name else "Anime"
    clean_title  = original_title.strip() if original_title else clean_anime
    clean_char   = character_name.strip().title() if character_name else ""

    # Fetch trending keywords for this anime
    trending = fetch_pinterest_keywords(anime_name)

    # Pick the best keyword to anchor the title
    # Prefer keywords containing "wallpaper" or "aesthetic" — highest search volume
    anchor_kw = ""
    for kw in trending:
        kw_lower = kw.lower()
        if any(x in kw_lower for x in ("wallpaper", "aesthetic", "art", "poster")):
            anchor_kw = kw.strip().title()
            break
    if not anchor_kw and trending:
        anchor_kw = trending[0].strip().title()

    # Build optimized title
    # Format: "AnimeName - Character | Original Title | SEO keyword"
    if clean_char:
        base = f"{clean_anime} - {clean_char}"
    else:
        base = clean_anime

    # Append anchor keyword if it adds value (not already in title)
    if anchor_kw and anchor_kw.lower() not in clean_title.lower():
        seo_title = f"{base} | {clean_title} | {anchor_kw}"
    else:
        seo_title = f"{base} | {clean_title}"

    # Enforce 100-char Pinterest limit
    if len(seo_title) > 100:
        # Truncate the original title part first, keep anime+character
        seo_title = f"{base} | {clean_title}"[:100]

    logger.info(f"[KeywordSEO] SEO title built: {seo_title!r}")
    return seo_title


def inject_keywords_into_description(
    description: str,
    anime_name: str,
    character_name: str = "",
    max_inject: int = _MAX_INJECT_KEYWORDS,
) -> str:
    """
    Injects top trending Pinterest keywords naturally into the pin description.

    Pinterest's algorithm reads the first 150 characters of a description most
    heavily. We prepend a natural-sounding keyword sentence at the top.

    Example output prepended:
      "Spy x Family anime wallpaper 4K | Anya aesthetic fan art | Anime poster"

    This does NOT replace hashtags — it works alongside hashtag_optimizer.py.
    """
    trending = fetch_pinterest_keywords(anime_name, max_results=8)
    if not trending:
        return description

    # Pick diverse keywords (avoid all being the same type)
    selected = trending[:max_inject]

    # Build a natural-sounding keyword line
    keyword_line = " | ".join(kw.strip().title() for kw in selected)

    # Prepend to description (Pinterest weights the opening heavily)
    enhanced = f"{keyword_line}\n\n{description}"
    logger.info(f"[KeywordSEO] Injected {len(selected)} keywords into description for '{anime_name}'")
    return enhanced


# ── Analytics-driven keyword prioritization ───────────────────────────────────

def get_top_performing_keywords(limit: int = 10) -> list[dict]:
    """
    Returns the keywords/anime names that drove the most affiliate clicks.
    Used to prioritize which anime content to post more of.
    Cross-references seo_keyword_cache with link_clicks analytics.
    """
    try:
        from database import _get_conn
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT lc.anime_name, COUNT(*) as clicks,
                       kc.keywords_json
                FROM link_clicks lc
                LEFT JOIN seo_keyword_cache kc
                  ON lower(lc.anime_name) = kc.anime_name
                WHERE lc.anime_name IS NOT NULL AND lc.anime_name != ''
                GROUP BY lc.anime_name
                ORDER BY clicks DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [
            {
                "anime":    r[0],
                "clicks":   r[1],
                "keywords": json.loads(r[2])[:3] if r[2] else [],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[KeywordSEO] get_top_performing_keywords failed: {e}")
        return []


def get_keyword_cache_stats() -> dict:
    """Returns stats about the cached keyword bank."""
    try:
        from database import _get_conn
        with _get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM seo_keyword_cache"
            ).fetchone()[0]
            fresh = conn.execute("""
                SELECT COUNT(*) FROM seo_keyword_cache
                WHERE datetime(fetched_at, '+24 hours') > datetime('now')
            """).fetchone()[0]
        return {"total_cached": total, "fresh": fresh, "stale": total - fresh}
    except Exception:
        return {"total_cached": 0, "fresh": 0, "stale": 0}
