"""
hashtag_optimizer.py
====================
Pinterest SEO Hashtag Optimizer — generates 12-15 targeted, trending hashtags
per pin based on anime name, genre, character, and product type.

Runs 100% offline (curated tag bank, no API needed).
Replaces the generic AI-generated hashtags with niche-specific, high-traffic tags.
"""

import re
from logger import get_logger

logger = get_logger(__name__)

# ── Genre-specific hashtag banks ─────────────────────────────────────────────

_GENRE_TAGS = {
    "shonen": [
        "#ShonenAnime", "#AnimeAction", "#AnimeFight", "#AnimeHero",
        "#AnimeAdventure", "#MangaArt", "#AnimeStrength",
    ],
    "isekai": [
        "#IsekaiAnime", "#OtherWorldAnime", "#AnimeFantasy", "#AnimeMagic",
        "#RPGAnime", "#MangaWorld", "#AnimeAdventure",
    ],
    "romance": [
        "#RomanceAnime", "#AnimeCouple", "#ShojouAnime", "#AnimeHeart",
        "#AnimeLove", "#CuteAnime", "#MangaRomance",
    ],
    "horror": [
        "#HorrorAnime", "#DarkAnime", "#AnimeCreepy", "#AnimeThriller",
        "#MangaHorror", "#AnimeGore", "#DarkManga",
    ],
    "mecha": [
        "#MechaAnime", "#RobotAnime", "#AnimeRobot", "#SciFiAnime",
        "#AnimeMecha", "#MangaMecha", "#AnimeScience",
    ],
    "sports": [
        "#SportsAnime", "#AnimeBasketball", "#AnimeVolleyball", "#AnimeSports",
        "#MangaSports", "#AnimeTeam", "#AnimeCompetition",
    ],
    "fantasy": [
        "#FantasyAnime", "#DarkFantasyAnime", "#AnimeFantasy", "#AnimeMagic",
        "#AnimeDragon", "#MangaFantasy", "#AnimeWorld",
    ],
    "general": [
        "#AnimeArt", "#MangaArt", "#AnimeLovers", "#AnimeCommunity",
        "#AnimeLife", "#AnimeWorld", "#AnimeFan",
    ],
}

# ── Product-type hashtag banks ────────────────────────────────────────────────

_PRODUCT_TAGS = {
    "poster":   ["#AnimePoster", "#AnimeWallArt", "#AnimeRoom", "#AnimeDecor", "#WallArtPrint"],
    "hoodie":   ["#AnimeHoodie", "#AnimeClothing", "#AnimeFashion", "#AnimeOutfit", "#AnimeMerchClothing"],
    "figure":   ["#AnimeFigure", "#AnimeCollectible", "#AnimeStatue", "#AnimeToy", "#AnimeCollection"],
    "keychain": ["#AnimeKeychain", "#AnimeAccessories", "#AnimeJewelry", "#AnimeGifts", "#AnimeSouvenirs"],
    "plush":    ["#AnimePlush", "#AnimeStuffed", "#AnimeCuddly", "#AnimeToy", "#AnimeGifts"],
    "artbook":  ["#AnimeArtbook", "#MangaBook", "#AnimeBook", "#AnimeArt", "#MangaCollection"],
    "sticker":  ["#AnimeSticker", "#AnimeStickerPack", "#AnimeDecal", "#AnimePrint", "#AnimeDesign"],
    "default":  ["#AnimeMerch", "#AnimeGifts", "#AnimeShop", "#AnimeProducts", "#BuyAnime"],
}

# ── Compliance + reach tags (always included) ─────────────────────────────────

_BASE_TAGS = ["#anime", "#animefan", "#otaku", "#ad", "#affiliate"]

# ── Series-specific tag bank (100+ anime) ────────────────────────────────────

_ANIME_TAGS = {
    # Shonen
    "demon slayer":         ["#DemonSlayer", "#KimetsuNoYaiba", "#Tanjiro", "#Nezuko", "#Zenitsu", "#Inosuke"],
    "naruto":               ["#Naruto", "#NarutoShippuden", "#Naruto", "#Sasuke", "#Kakashi", "#Hokage"],
    "attack on titan":      ["#AttackOnTitan", "#ShingekiNoKyojin", "#SNK", "#Eren", "#Levi", "#Mikasa"],
    "my hero academia":     ["#MyHeroAcademia", "#BokuNoHeroAcademia", "#MHA", "#Deku", "#AllMight", "#Bakugo"],
    "one piece":            ["#OnePiece", "#Luffy", "#Zoro", "#Nami", "#Sanji", "#StrawHatPirates"],
    "dragon ball":          ["#DragonBall", "#DragonBallZ", "#DragonBallSuper", "#Goku", "#Vegeta", "#DBZ"],
    "bleach":               ["#Bleach", "#BleachTBTB", "#Ichigo", "#Rukia", "#Byakuya", "#Zangetsu"],
    "jujutsu kaisen":       ["#JujutsuKaisen", "#JJK", "#Gojo", "#Itadori", "#Megumi", "#Nobara"],
    "black clover":         ["#BlackClover", "#Asta", "#Yuno", "#BlackBulls", "#MagicKnights"],
    "fairy tail":           ["#FairyTail", "#Natsu", "#Erza", "#Gray", "#Lucy", "#FairyTailGuild"],
    "hunter x hunter":      ["#HunterXHunter", "#HxH", "#Gon", "#Killua", "#Kurapika", "#Hisoka"],
    "fullmetal alchemist":  ["#FullmetalAlchemist", "#FMA", "#FMAB", "#EdwardElric", "#Alphonse", "#RoyMustang"],
    "chainsaw man":         ["#ChainsawMan", "#Denji", "#PowerChainsawMan", "#Makima", "#AkiHayakawa"],
    "tokyo revengers":      ["#TokyoRevengers", "#TakedaMikey", "#Draken", "#TokyoRevengers"],
    "spy x family":         ["#SpyXFamily", "#Anya", "#Loid", "#Yor", "#SpyFamily"],
    "haikyuu":              ["#Haikyuu", "#Shoyo", "#Kageyama", "#Haikyu", "#VolleyballAnime"],
    "blue lock":            ["#BlueLock", "#IsagiYoichi", "#SoccerAnime", "#BlueLockAnime"],
    "vinland saga":         ["#VinlandSaga", "#Thorfinn", "#Askeladd", "#VikingAnime"],
    # Isekai
    "sword art online":     ["#SwordArtOnline", "#SAO", "#Kirito", "#Asuna", "#Aincrad"],
    "re:zero":              ["#ReZero", "#Subaru", "#Emilia", "#Rem", "#Ram", "#ReZeroAnime"],
    "overlord":             ["#Overlord", "#Ainz", "#Albedo", "#Shalltear", "#OverlordAnime"],
    "that time i got reincarnated as a slime": ["#TenSura", "#Rimuru", "#SlimeAnime", "#ThatTimeAnime"],
    "konosuba":             ["#Konosuba", "#Kazuma", "#Aqua", "#Megumin", "#Darkness", "#KonoSubaAnime"],
    "mushoku tensei":       ["#MushokuTensei", "#Rudeus", "#IsekaiAnimeTop", "#MushokuTenseiAnime"],
    "rising of the shield hero": ["#ShieldHero", "#Naofumi", "#RisingShieldHero", "#TateNoYuusha"],
    "no game no life":      ["#NoGameNoLife", "#Sora", "#Shiro", "#NGNL", "#GamingAnime"],
    "danmachi":             ["#DanMachi", "#BellCranel", "#Hestia", "#IsItWrongAnime"],
    # Romance
    "your name":            ["#YourName", "#KimiNoNawa", "#Taki", "#Mitsuha", "#MakotoShinkai"],
    "a silent voice":       ["#ASilentVoice", "#KoeNoKatachi", "#Shoya", "#Shoko"],
    "toradora":             ["#Toradora", "#Taiga", "#Ryuji", "#TigerDragonAnime"],
    "clannad":              ["#Clannad", "#ClannadAfterStory", "#Tomoya", "#Nagisa", "#ClannadAnime"],
    "anohana":              ["#AnoHana", "#Menma", "#Jintan", "#AnoHanaAnime"],
    "weathering with you":  ["#WeatheringWithYou", "#Tenkinoko", "#MakotoShinkai", "#WeatherAnime"],
    "fruits basket":        ["#FruitsBasket", "#Tohru", "#Kyo", "#Yuki", "#FruitBasketAnime"],
    # Horror / Dark
    "tokyo ghoul":          ["#TokyoGhoul", "#Kaneki", "#Touka", "#Ghoul", "#TokyoGhoulAnime"],
    "another":              ["#Another", "#AnotherAnime", "#HorrorAnime", "#MeiMisaki"],
    "parasyte":             ["#Parasyte", "#Shinichi", "#Migi", "#ParasyteAnime"],
    # Mecha
    "neon genesis evangelion": ["#Evangelion", "#NGE", "#Shinji", "#Rei", "#Asuka", "#EVA"],
    "gurren lagann":        ["#GurrenLagann", "#SimonGurren", "#Kamina", "#Yoko", "#GurrenLagannAnime"],
    "code geass":           ["#CodeGeass", "#Lelouch", "#CC", "#Suzaku", "#CodeGeassAnime"],
    # Fantasy / Other
    "made in abyss":        ["#MadeInAbyss", "#Riko", "#Reg", "#Nanachi", "#MadeInAbyssAnime"],
    "violet evergarden":    ["#VioletEvergarden", "#Violet", "#GilbertBougainvillea", "#VioletAnime"],
    "steins gate":          ["#SteinsGate", "#Okabe", "#Kurisu", "#Mayuri", "#SteinsGateAnime"],
    "death note":           ["#DeathNote", "#LightYagami", "#L", "#Ryuk", "#DeathNoteAnime"],
    "cowboy bebop":         ["#CowboyBebop", "#Spike", "#Faye", "#JetBlack", "#CowboyBebopAnime"],
    "one punch man":        ["#OnePunchMan", "#Saitama", "#Genos", "#OPM", "#OnePunchManAnime"],
    "mob psycho 100":       ["#MobPsycho100", "#Mob", "#Reigen", "#MobPsycho"],
    "demon slayer kimetsu no yaiba": ["#DemonSlayer", "#KimetsuNoYaiba", "#Tanjiro", "#Nezuko"],
    "ao no exorcist":       ["#BlueExorcist", "#RinOkumura", "#AoNoExorcist", "#SatanSon"],
}


def _detect_product_type(product_hint: str) -> str:
    """Detect product category from the product hint string."""
    h = product_hint.lower()
    if any(w in h for w in ["poster", "wall art", "print"]):
        return "poster"
    if any(w in h for w in ["hoodie", "shirt", "clothing", "jacket"]):
        return "hoodie"
    if any(w in h for w in ["figure", "action figure", "statue", "collectible"]):
        return "figure"
    if any(w in h for w in ["keychain", "key chain", "accessory"]):
        return "keychain"
    if any(w in h for w in ["plush", "stuffed", "soft toy"]):
        return "plush"
    if any(w in h for w in ["artbook", "art book", "book", "manga"]):
        return "artbook"
    if any(w in h for w in ["sticker", "decal"]):
        return "sticker"
    return "default"


def _get_anime_specific_tags(anime_name: str) -> list:
    """Looks up series-specific tags for the given anime."""
    key = anime_name.lower().strip()
    # Exact match
    if key in _ANIME_TAGS:
        return _ANIME_TAGS[key]
    # Partial match — check if any known key appears in the anime name
    for known_key, tags in _ANIME_TAGS.items():
        if known_key in key or key in known_key:
            return tags
    # Fallback: generate basic tags from the anime name itself
    safe_name = re.sub(r"[^a-zA-Z0-9\s]", "", anime_name).title().replace(" ", "")
    return [f"#{safe_name}", f"#{safe_name}Anime", f"#{safe_name}Merch"]


def optimize_hashtags(
    anime_name: str,
    genre: str = "general",
    character_name: str = "",
    product_hint: str = "anime merch",
) -> str:
    """
    Generates 12-15 Pinterest SEO-optimized hashtags for a pin.

    Args:
        anime_name:     e.g. "Demon Slayer"
        genre:          e.g. "shonen", "isekai", "romance" (from board_router)
        character_name: e.g. "Tanjiro" (extracted from AI title)
        product_hint:   e.g. "anime poster", "anime hoodie"

    Returns:
        A formatted hashtag string ready to append to a pin description.
    """
    tags = []

    # Layer 1: Series-specific tags (3-5 tags, most targeted)
    anime_tags = _get_anime_specific_tags(anime_name)
    tags.extend(anime_tags[:4])

    # Add character tag if we have one and it's not already in anime_tags
    if character_name and character_name.strip():
        char_tag = f"#{character_name.strip().split()[0].title()}"
        if char_tag not in tags:
            tags.append(char_tag)

    # Layer 2: Genre-specific tags (3-4 tags)
    genre_key = genre.lower() if genre.lower() in _GENRE_TAGS else "general"
    genre_tag_pool = _GENRE_TAGS[genre_key]
    tags.extend(genre_tag_pool[:3])

    # Layer 3: Product-type tags (2-3 tags)
    product_key = _detect_product_type(product_hint)
    product_tag_pool = _PRODUCT_TAGS.get(product_key, _PRODUCT_TAGS["default"])
    tags.extend(product_tag_pool[:2])

    # Layer 4: Base tags (always present — compliance + reach)
    for tag in _BASE_TAGS:
        if tag not in tags:
            tags.append(tag)

    # Deduplicate while preserving order, cap at 15
    seen = set()
    final_tags = []
    for tag in tags:
        if tag.lower() not in seen:
            seen.add(tag.lower())
            final_tags.append(tag)
        if len(final_tags) >= 15:
            break

    result = " ".join(final_tags)
    logger.info(f"[Hashtag] Generated {len(final_tags)} tags for '{anime_name}' ({genre}): {result}")
    return result


def replace_hashtags_in_description(description: str, optimized_tags: str) -> str:
    """
    Replaces the AI-generated hashtag block at the end of the description
    with the optimized hashtag set.

    Finds the last occurrence of a hashtag line and replaces everything after it.
    Falls back to appending if no hashtag block found.
    """
    # Find position of first hashtag in the last hashtag block
    lines = description.split("\n")
    last_hashtag_line = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        # A "hashtag line" is a line where most tokens start with #
        tokens = stripped.split()
        if tokens and sum(1 for t in tokens if t.startswith("#")) >= len(tokens) * 0.5:
            last_hashtag_line = i

    if last_hashtag_line >= 0:
        # Keep everything before the first hashtag line, replace rest
        # Find the START of the hashtag block (contiguous from last_hashtag_line upward)
        block_start = last_hashtag_line
        while block_start > 0:
            prev = lines[block_start - 1].strip()
            prev_tokens = prev.split()
            if prev_tokens and sum(1 for t in prev_tokens if t.startswith("#")) >= len(prev_tokens) * 0.5:
                block_start -= 1
            else:
                break

        body = "\n".join(lines[:block_start]).rstrip()
        return f"{body}\n\n{optimized_tags}"
    else:
        # No hashtag block found — just append
        return f"{description.rstrip()}\n\n{optimized_tags}"
