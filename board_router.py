"""
board_router.py
===============
Routes each anime pin to the correct Pinterest board based on genre.

Board mapping is configured via .env variables:
    BOARD_ID_SHONEN   → Action/battle anime board
    BOARD_ID_ISEKAI   → Isekai/fantasy anime board
    BOARD_ID_ROMANCE  → Romance/slice-of-life board
    BOARD_ID_GENERAL  → Default fallback board (your main board)

Falls back to PINTEREST_BOARD_ID if genre-specific board not configured.
"""

import os
from dotenv import load_dotenv
from logger import get_logger

load_dotenv()
logger = get_logger(__name__)

# ── Board ID config from .env ─────────────────────────────────────────────────

_BOARD_IDS = {
    "shonen":  os.getenv("BOARD_ID_SHONEN",  ""),
    "isekai":  os.getenv("BOARD_ID_ISEKAI",  ""),
    "romance": os.getenv("BOARD_ID_ROMANCE", ""),
    "horror":  os.getenv("BOARD_ID_HORROR",  ""),
    "mecha":   os.getenv("BOARD_ID_MECHA",   ""),
    "sports":  os.getenv("BOARD_ID_SPORTS",  ""),
    "fantasy": os.getenv("BOARD_ID_FANTASY", ""),
    "general": os.getenv("BOARD_ID_GENERAL", os.getenv("PINTEREST_BOARD_ID", "")),
}

# ── Anime → Genre mapping (200+ series) ──────────────────────────────────────

_ANIME_GENRE = {
    # ── Shonen (Action / Battle) ──────────────────────────────────────────
    "demon slayer":                             "shonen",
    "kimetsu no yaiba":                         "shonen",
    "naruto":                                   "shonen",
    "naruto shippuden":                         "shonen",
    "boruto":                                   "shonen",
    "attack on titan":                          "shonen",
    "shingeki no kyojin":                       "shonen",
    "my hero academia":                         "shonen",
    "boku no hero academia":                    "shonen",
    "one piece":                                "shonen",
    "dragon ball":                              "shonen",
    "dragon ball z":                            "shonen",
    "dragon ball super":                        "shonen",
    "bleach":                                   "shonen",
    "bleach tybw":                              "shonen",
    "jujutsu kaisen":                           "shonen",
    "black clover":                             "shonen",
    "fairy tail":                               "shonen",
    "hunter x hunter":                          "shonen",
    "fullmetal alchemist":                      "shonen",
    "fullmetal alchemist brotherhood":          "shonen",
    "chainsaw man":                             "shonen",
    "tokyo revengers":                          "shonen",
    "one punch man":                            "shonen",
    "mob psycho 100":                           "shonen",
    "blue lock":                                "shonen",
    "vinland saga":                             "shonen",
    "solo leveling":                            "shonen",
    "black clover":                             "shonen",
    "yu yu hakusho":                            "shonen",
    "inuyasha":                                 "shonen",
    "rurouni kenshin":                          "shonen",
    "saint seiya":                              "shonen",
    "fist of the north star":                   "shonen",
    "sword art online":                         "shonen",
    # ── Isekai ────────────────────────────────────────────────────────────
    "re:zero":                                  "isekai",
    "rezero":                                   "isekai",
    "overlord":                                 "isekai",
    "that time i got reincarnated as a slime":  "isekai",
    "tensura":                                  "isekai",
    "konosuba":                                 "isekai",
    "mushoku tensei":                           "isekai",
    "the rising of the shield hero":            "isekai",
    "no game no life":                          "isekai",
    "danmachi":                                 "isekai",
    "is it wrong to try to pick up girls in a dungeon": "isekai",
    "log horizon":                              "isekai",
    "accel world":                              "isekai",
    "the devil is a part-timer":                "isekai",
    "anime 43":                                 "isekai",
    "arifureta":                                "isekai",
    "in another world with my smartphone":      "isekai",
    "isekai quartet":                           "isekai",
    "the eminence in shadow":                   "isekai",
    "frieren beyond journeys end":              "isekai",
    "frieren: beyond journey's end":            "isekai",
    "frieren beyond journey's end":             "isekai",
    "sousou no frieren":                        "isekai",
    "frieren":                                  "isekai",
    "dungeon meshi":                            "isekai",
    "delicious in dungeon":                     "isekai",
    "tensai ouji":                              "isekai",
    # ── Romance / Slice of Life ───────────────────────────────────────────
    "your name":                                "romance",
    "kimi no na wa":                            "romance",
    "a silent voice":                           "romance",
    "koe no katachi":                           "romance",
    "toradora":                                 "romance",
    "clannad":                                  "romance",
    "anohana":                                  "romance",
    "ano hi mita hana":                         "romance",
    "weathering with you":                      "romance",
    "tenki no ko":                              "romance",
    "fruits basket":                            "romance",
    "fruit basket":                             "romance",
    "ouran high school host club":              "romance",
    "kaichou wa maid sama":                     "romance",
    "special a":                                "romance",
    "ao haru ride":                             "romance",
    "your lie in april":                        "romance",
    "shigatsu wa kimi no uso":                  "romance",
    "say i love you":                           "romance",
    "domestic girlfriend":                      "romance",
    "rent a girlfriend":                        "romance",
    "my dress-up darling":                      "romance",
    "horimiya":                                 "romance",
    "spy x family":                             "romance",
    "kaguya-sama love is war":                  "romance",
    "kaguya-sama: love is war":                 "romance",
    "kaguya sama love is war":                  "romance",
    "kaguya-sama":                              "romance",
    "love is war":                              "romance",
    "nagatoro":                                 "romance",
    "takagi-san":                               "romance",
    "oregairu":                                 "romance",
    "my youth romantic comedy":                 "romance",
    "gamers":                                   "romance",
    "classroom of the elite":                   "romance",
    # ── Horror / Dark ─────────────────────────────────────────────────────
    "tokyo ghoul":                              "horror",
    "another":                                  "horror",
    "parasyte":                                 "horror",
    "higurashi":                                "horror",
    "when they cry":                            "horror",
    "elfen lied":                               "horror",
    "corpse party":                             "horror",
    "shiki":                                    "horror",
    "dusk maiden of amnesia":                   "horror",
    "made in abyss":                            "horror",
    "promised neverland":                       "horror",
    "the promised neverland":                   "horror",
    "devilman crybaby":                         "horror",
    "hellsing":                                 "horror",
    "hellsing ultimate":                        "horror",
    # ── Mecha ─────────────────────────────────────────────────────────────
    "neon genesis evangelion":                  "mecha",
    "evangelion":                               "mecha",
    "gurren lagann":                            "mecha",
    "tengen toppa gurren lagann":               "mecha",
    "code geass":                               "mecha",
    "gundam":                                   "mecha",
    "mobile suit gundam":                       "mecha",
    "aldnoah zero":                             "mecha",
    "darling in the franxx":                    "mecha",
    "eureka seven":                             "mecha",
    "macross":                                  "mecha",
    # ── Sports ────────────────────────────────────────────────────────────
    "haikyuu":                                  "sports",
    "haikyu":                                   "sports",
    "kuroko no basket":                         "sports",
    "kuroko's basketball":                      "sports",
    "slam dunk":                                "sports",
    "captain tsubasa":                          "sports",
    "yowamushi pedal":                          "sports",
    "initial d":                                "sports",
    "megalobox":                                "sports",
    "free":                                     "sports",
    "free iwatobi swim club":                   "sports",
    # ── Fantasy / Other ───────────────────────────────────────────────────
    "violet evergarden":                        "fantasy",
    "steins gate":                              "fantasy",
    "death note":                               "fantasy",
    "cowboy bebop":                             "fantasy",
    "trigun":                                   "fantasy",
    "fullmetal panic":                          "fantasy",
    "ao no exorcist":                           "fantasy",
    "blue exorcist":                            "fantasy",
    "noragami":                                 "fantasy",
    "magi":                                     "fantasy",
    "black butler":                             "fantasy",
    "kuroshitsuji":                             "fantasy",
    "seven deadly sins":                        "fantasy",
    "nanatsu no taizai":                        "fantasy",
    "re creators":                              "fantasy",
    "tower of god":                             "fantasy",
    "god of high school":                       "fantasy",
    "the misfit of demon king academy":         "fantasy",
    # ── Fate Series ────────────────────────────────────────────────────────
    "fate":                                     "fantasy",
    "fate/stay night":                          "fantasy",
    "fatestay night":                           "fantasy",
    "fate stay night":                          "fantasy",
    "fate/zero":                                "fantasy",
    "fate zero":                                "fantasy",
    "fate/grand order":                         "fantasy",
    "fate grand order":                         "fantasy",
    "fate/apocrypha":                           "fantasy",
    "heavens feel":                             "fantasy",
    "heaven's feel":                            "fantasy",
    "unlimited blade works":                    "fantasy",
}


def get_genre_for_anime(anime_name: str) -> str:
    """
    Returns the genre string for a given anime name.
    Falls back to 'general' if not found.
    """
    key = anime_name.lower().strip()

    # Exact match
    if key in _ANIME_GENRE:
        return _ANIME_GENRE[key]

    # Partial match — check if any known key is a substring of the anime name
    for known_key, genre in _ANIME_GENRE.items():
        if known_key in key:
            return genre

    logger.info(f"[BoardRouter] No genre match for '{anime_name}' — defaulting to 'general'")
    return "general"


def get_board_for_anime(anime_name: str) -> tuple[str, str]:
    """
    Returns (genre, board_id) for the given anime name.

    Uses genre-specific board IDs from .env, falls back through:
      genre board → general board → PINTEREST_BOARD_ID → empty string

    Args:
        anime_name: The anime series name (e.g. "Demon Slayer")

    Returns:
        (genre: str, board_id: str)
    """
    genre = get_genre_for_anime(anime_name)
    board_id = _BOARD_IDS.get(genre, "")

    # 1. Genre-specific board configured
    if board_id:
        logger.info(f"[BoardRouter] '{anime_name}' -> genre='{genre}' -> board_id='{board_id}' (genre board)")
        return genre, board_id

    # 2. Fall back to general board / PINTEREST_BOARD_ID
    board_id = _BOARD_IDS.get("general", "")
    if board_id:
        logger.info(f"[BoardRouter] '{anime_name}' -> genre='{genre}' -> board_id='{board_id}' (fallback to general board)")
    else:
        logger.warning(
            f"[BoardRouter] '{anime_name}' -> genre='{genre}' "
            f"(no board configured, uploader will use default)"
        )

    return genre, board_id


# ── Canonical Anime Name Extraction & Sanitization ──────────────────────────

_SORTED_KNOWN_ANIME = sorted(_ANIME_GENRE.keys(), key=len, reverse=True)

def extract_canonical_anime_name(raw_name: str, caption: str = "") -> str:
    """
    Extracts a clean, canonical anime series name from potentially dirty input.
    Handles:
      1. AI hallucinations / placeholders (e.g. '<specific anime series name>')
      2. Concatenated quotes from Telegram captions (e.g. 'He saw the ruined wall...Attack on Titan')
      3. Taxonomy matching against 200+ series in _ANIME_GENRE.
    """
    import re

    # 1. Reject obvious placeholder text
    if not raw_name or "<" in raw_name or ">" in raw_name:
        raw_name = ""

    # 2. Check known anime taxonomy against raw_name first (longest match first)
    if raw_name:
        clean_raw = raw_name.lower().strip()
        for known in _SORTED_KNOWN_ANIME:
            # Word boundary or substring match
            pattern = r'(?:\b|[._\-])' + re.escape(known) + r'(?:\b|[._\-]|$)'
            if re.search(pattern, clean_raw) or known in clean_raw:
                return known.title()

    # 3. If raw_name did not match, check caption if provided
    if caption:
        clean_cap = caption.lower().strip()
        for known in _SORTED_KNOWN_ANIME:
            pattern = r'(?:\b|[._\-])' + re.escape(known) + r'(?:\b|[._\-]|$)'
            if re.search(pattern, clean_cap) or known in clean_cap:
                return known.title()

    # 4. If no taxonomy match, try to clean the raw_name
    if raw_name:
        # If it has sentences/quotes, take the last segment after period/newline/dash
        candidate = raw_name
        for delim in ["\n", ".", "!", "?", " - ", ": "]:
            if delim in candidate:
                parts = [p.strip() for p in candidate.split(delim) if p.strip()]
                if parts:
                    candidate = parts[-1]

        candidate = re.sub(r'[\'\"«»“”\<\>\[\]\{\}]', '', candidate).strip()
        # If reasonable title length (3 to 35 chars) and doesn't look like a sentence
        if 3 <= len(candidate) <= 35:
            # Check if it starts with common sentence starters
            first_word = candidate.lower().split()[0] if candidate.split() else ""
            if first_word not in ("he", "she", "they", "we", "i", "it", "when", "after", "before", "the", "a", "saving", "some", "her", "his"):
                return candidate.title()

    return "Anime"

