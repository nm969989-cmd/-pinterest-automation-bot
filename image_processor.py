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
    Processes the downloaded image to make it optimal for Pinterest.
    - Resizes to 1000x1500 (2:3 aspect ratio) padding or cropping as needed.
    - Optionally adds a watermark for the source channel.
    Returns the path to the processed image.
    """
    try:
        logger.info(f"Processing image: {filepath}")
        img = Image.open(filepath)
        
        # Convert to RGB if it's RGBA or P
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
            
        # Resize logic (crop to fill 2:3 ratio)
        img_w, img_h = img.size
        target_ratio = PINTEREST_WIDTH / PINTEREST_HEIGHT
        current_ratio = img_w / img_h
        
        if current_ratio > target_ratio:
            # Image is wider than 2:3, crop width
            new_w = int(img_h * target_ratio)
            left = (img_w - new_w) / 2
            right = (img_w + new_w) / 2
            img = img.crop((left, 0, right, img_h))
        elif current_ratio < target_ratio:
            # Image is taller than 2:3, crop height
            new_h = int(img_w / target_ratio)
            top = (img_h - new_h) / 2
            bottom = (img_h + new_h) / 2
            img = img.crop((0, top, img_w, bottom))
            
        # Resize to exactly 1000x1500
        img = img.resize((PINTEREST_WIDTH, PINTEREST_HEIGHT), Image.Resampling.LANCZOS)
        
        # No watermark — clean professional pins with full artwork visible

        filename = os.path.basename(filepath)
        processed_path = os.path.join('processed', filename)
        
        # Save as JPEG for better compression
        if not processed_path.lower().endswith(('.jpg', '.jpeg')):
            processed_path = os.path.splitext(processed_path)[0] + '.jpg'
            
        img.save(processed_path, 'JPEG', quality=90)
        logger.info(f"Successfully processed and saved to: {processed_path}")
        return processed_path
        
    except Exception as e:
        logger.error(f"Error processing image {filepath}: {str(e)}")
        return filepath # Return original if processing fails
