import os
from PIL import Image
from logger import get_logger

logger = get_logger(__name__)

# Constants for Pinterest optimal size
PINTEREST_WIDTH = 1000
PINTEREST_HEIGHT = 1500

if not os.path.exists('downloads'):
    os.makedirs('downloads')
if not os.path.exists('processed'):
    os.makedirs('processed')

def process_image(filepath, channel_name=None):
    """
    Processes the downloaded image for Pinterest while preserving
    100% full original aspect ratio and dimensions (ZERO CROPPING).
    - Converts RGBA/P to clean RGB JPEG.
    - If dimensions > 2400px, downscales proportionally to max 2400px (Lanczos).
    - Otherwise keeps 100% original sharp resolution.
    - Explicitly closes image streams and runs garbage collection for Render Free Tier RAM safety.
    """
    img = None
    resized_img = None
    try:
        logger.info(f"Processing image: {filepath}")
        img = Image.open(filepath)
        
        # Convert to RGB if it's RGBA or P
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
            
        img_w, img_h = img.size
        
        # Proportional resize only if excessively large (> 2400px), maintaining exact aspect ratio
        MAX_DIM = 2400
        if img_w > MAX_DIM or img_h > MAX_DIM:
            if img_w >= img_h:
                new_w = MAX_DIM
                new_h = int((img_h / img_w) * MAX_DIM)
            else:
                new_h = MAX_DIM
                new_w = int((img_w / img_h) * MAX_DIM)
            resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            target_to_save = resized_img
            logger.info(f"Proportionally scaled large image: {img_w}x{img_h} -> {new_w}x{new_h} (0% cropped)")
        else:
            target_to_save = img
            logger.info(f"Preserved exact original dimensions: {img_w}x{img_h} (0% cropped)")

        filename = os.path.basename(filepath)
        processed_path = os.path.join('processed', filename)
        
        # Save as JPEG with high quality
        if not processed_path.lower().endswith(('.jpg', '.jpeg')):
            processed_path = os.path.splitext(processed_path)[0] + '.jpg'
            
        target_to_save.save(processed_path, 'JPEG', quality=92, optimize=True)
        logger.info(f"Successfully saved full original size image to: {processed_path}")

        # Inpaint and clean any Telegram channel watermarks or stamps
        try:
            from watermark_inpainter import clean_watermark
            cleaned, clean_path = clean_watermark(processed_path)
            if cleaned:
                logger.info(f"Watermark erased from processed image: {clean_path}")
        except Exception as _wm_err:
            logger.warning(f"Watermark inpainter non-critical error: {_wm_err}")

        return processed_path
        
    except Exception as e:
        logger.error(f"Error processing image {filepath}: {str(e)}")
        return filepath # Return original if processing fails
    finally:
        # Strict memory cleanup for Render 512MB RAM limit
        try:
            if img:
                img.close()
            if resized_img:
                resized_img.close()
        except Exception:
            pass
        import gc
        gc.collect()


def extract_image_color_tags(filepath: str) -> list[str]:
    """
    Extracts high-intent visual search hashtags from an image based on:
    1. Average luminance (dark / AMOLED / light / pastel).
    2. Dominant color hue (neon green, crimson red, midnight blue, violet, golden, monochrome).

    Zero external ML dependencies; executes in <3ms using a 50x50 thumbnail in memory.
    """
    if not filepath or not os.path.isfile(filepath):
        return []

    import colorsys
    tags = []
    img = None
    try:
        img = Image.open(filepath)
        # Downsample to 50x50 RGB thumbnail in memory for speed & zero RAM bloat
        thumb = img.convert("RGB").resize((50, 50), Image.Resampling.BILINEAR)
        try:
            pixels = [thumb.getpixel((x, y)) for y in range(50) for x in range(50)]
        except Exception:
            pixels = list(thumb.getdata())

        total_lum = 0
        hue_bins = {
            "red": 0,
            "orange_yellow": 0,
            "green": 0,
            "cyan": 0,
            "blue": 0,
            "purple": 0,
            "pink": 0,
        }
        sat_sum = 0

        for r, g, b in pixels:
            # Perceived luminance (ITU-R BT.601)
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            total_lum += lum

            # Convert RGB (0..1) to HSV
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            sat_sum += s

            # Only count hue if saturation is noticeable and not near pure black/white
            if s >= 0.18 and 0.12 <= v <= 0.95:
                deg = h * 360.0
                if deg < 15 or deg >= 345:
                    hue_bins["red"] += 1
                elif 15 <= deg < 70:
                    hue_bins["orange_yellow"] += 1
                elif 70 <= deg < 165:
                    hue_bins["green"] += 1
                elif 165 <= deg < 205:
                    hue_bins["cyan"] += 1
                elif 205 <= deg < 265:
                    hue_bins["blue"] += 1
                elif 265 <= deg < 315:
                    hue_bins["purple"] += 1
                elif 315 <= deg < 345:
                    hue_bins["pink"] += 1

        n_pixels = len(pixels)
        avg_lum = total_lum / n_pixels
        avg_sat = sat_sum / n_pixels

        # 1. Luminance tags
        if avg_lum < 55:
            tags.append("#DarkAnimeAesthetic")
            tags.append("#AMOLEDWallpaper")
        elif avg_lum > 185:
            tags.append("#LightAnimeAesthetic")
            tags.append("#PastelAesthetic")

        # 2. Saturation & Dominant Color tags
        if avg_sat < 0.15:
            tags.append("#MonochromeAesthetic")
            tags.append("#MangaArt")
        else:
            dominant_color = max(hue_bins, key=hue_bins.get)
            if hue_bins[dominant_color] > (n_pixels * 0.10):
                color_map = {
                    "green": ["#NeonGreen", "#GreenAesthetic", "#EmeraldAnime"],
                    "red": ["#CrimsonRed", "#RedAnimeAesthetic", "#DarkRedAesthetic"],
                    "blue": ["#MidnightBlue", "#BlueAnimeAesthetic", "#DarkBlueVibes"],
                    "purple": ["#NeonPurple", "#DarkPurpleAnime", "#VioletAesthetic"],
                    "orange_yellow": ["#GoldenAnime", "#OrangeAesthetic", "#GoldWallpaper"],
                    "cyan": ["#CyanAesthetic", "#TealAnimeVibes"],
                    "pink": ["#PastelPink", "#PinkAnimeAesthetic"],
                }
                tags.extend(color_map.get(dominant_color, []))

        # Deduplicate and return top 2-3 tags
        seen = set()
        res = []
        for t in tags:
            if t.lower() not in seen:
                seen.add(t.lower())
                res.append(t)
        return res[:3]
    except Exception as e:
        logger.warning(f"[ColorTag] Error extracting color tags from {filepath}: {e}")
        return []
    finally:
        if img:
            try:
                img.close()
            except Exception:
                pass


# ── Anti-Marketing & Ad Safeguard Engine ─────────────────────────────────────

AD_PROMO_KEYWORDS = [
    "advertise on", "deal directly with channel", "escrow protected",
    "people reached", "skip the ad review", "ads budget", "side income",
    "channel promo", "grow my channel", "grow your channel", "go live in 60 seconds",
    "subscribers promo", "paid promo", "sponsored post", "crypto trading",
    "forex trading", "casino bonus", "betting tips", "airdrop claim",
    "discount code", "click the link below", "dm to buy", "contact @",
    "buy promo", "ad review queue", "monetize your", "make money online",
    "passive income", "earn $", "earn €", "earn ₹", "investment opportunity",
    "referral link", "join my vip", "vip signals", "binary options",
    "official sponsor", "telegram ads platform", "paid sponsorship",
    "dm for promo", "promo exchange", "shoutout for shoutout", "s4s"
]


def is_promotional_caption(caption: str) -> tuple[bool, str]:
    """
    Checks if a post caption contains marketing, advertising, or spam patterns.
    Returns (is_promo: bool, reason: str).
    """
    if not caption or not isinstance(caption, str):
        return False, ""

    cap_lower = caption.lower()
    for kw in AD_PROMO_KEYWORDS:
        if kw in cap_lower:
            return True, f"Caption contains promotional phrase '{kw}'"

    # Match URLs with ad/tracking or promo query parameters
    if any(param in cap_lower for param in ("t.me/+", "bit.ly/", "tinyurl.com/", "ref=", "aff=")):
        return True, "Caption contains promotional or referral redirect URL"

    return False, ""


def is_marketing_or_ad_image(filepath: str, caption: str = "") -> tuple[bool, str]:
    """
    Multi-layer computer vision & heuristic safeguard against marketing graphics,
    advertisement banners, app screenshots, and promotional flyers.
    
    Layers:
      1. Fast Caption Ad Keyword Check (0ms)
      2. Computer Vision Text Density & Bounding Box Analysis (OpenCV / PIL, ~10ms)
      3. UI / Flat Card Background Dominance Check
      4. Decision Boundary Check
    
    Returns:
      (is_ad: bool, reason: str)
    """
    if not filepath or not os.path.isfile(filepath):
        return False, ""

    # Layer 1: Caption keyword heuristic
    if caption:
        is_promo, promo_reason = is_promotional_caption(caption)
        if is_promo:
            return True, promo_reason

    # Layer 2: Computer Vision Text Density & Background Dominance Analysis
    try:
        import cv2
        import numpy as np

        img = cv2.imread(filepath)
        if img is None:
            return False, ""

        h, w = img.shape[:2]
        total_pixels = h * w
        if total_pixels == 0:
            return False, ""

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # A. Text region bounding box clustering
        grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

        kw = max(9, int(w / 50))
        kh = max(3, int(h / 300))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))
        connected = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        text_boxes = []
        total_text_area = 0
        for cnt in contours:
            x, y, bw_box, bh_box = cv2.boundingRect(cnt)
            aspect = bw_box / float(bh_box)
            area = bw_box * bh_box
            if 0.0002 * total_pixels < area < 0.25 * total_pixels:
                if aspect > 1.2 or (0.5 <= aspect <= 1.2 and area < 0.01 * total_pixels):
                    text_boxes.append((x, y, bw_box, bh_box))
                    total_text_area += area

        text_coverage = total_text_area / float(total_pixels)
        text_count = len(text_boxes)

        # B. Flat Background / Solid Card Dominance
        small = cv2.resize(gray, (100, 100))
        hist = cv2.calcHist([small], [0], None, [32], [0, 256])
        flat_dominance = float(np.max(hist)) / 10000.0

        # C. Decision Rules
        # Rule 1: High text coverage (Flyers, text-heavy advertisements)
        if text_coverage > 0.11:
            return True, f"High text density ({text_coverage:.1%}) characteristic of marketing banner"

        # Rule 2: Moderate text coverage combined with heavy flat UI/card dominance (App UI screenshot / Ad card)
        if flat_dominance > 0.45 and text_coverage > 0.035 and text_count >= 5:
            return True, f"UI/Ad card layout detected (flat background {flat_dominance:.1%}, text coverage {text_coverage:.1%}, {text_count} text blocks)"

        # Rule 3: Extreme flat background with text elements (Infographic / Promo announcement)
        if flat_dominance > 0.70 and text_count >= 4:
            return True, f"Flat background banner layout detected ({flat_dominance:.1%} flat color with {text_count} text elements)"

        return False, ""

    except Exception as e:
        logger.debug(f"[AdSafeguard] CV analysis non-critical error: {e}")
        return False, ""



