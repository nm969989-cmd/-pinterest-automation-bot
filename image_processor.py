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
    - No pixels cropped.
    """
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
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            logger.info(f"Proportionally scaled large image: {img_w}x{img_h} -> {new_w}x{new_h} (0% cropped)")
        else:
            logger.info(f"Preserved exact original dimensions: {img_w}x{img_h} (0% cropped)")

        filename = os.path.basename(filepath)
        processed_path = os.path.join('processed', filename)
        
        # Save as JPEG with high quality
        if not processed_path.lower().endswith(('.jpg', '.jpeg')):
            processed_path = os.path.splitext(processed_path)[0] + '.jpg'
            
        img.save(processed_path, 'JPEG', quality=92, optimize=True)
        logger.info(f"Successfully saved full original size image to: {processed_path}")
        return processed_path
        
    except Exception as e:
        logger.error(f"Error processing image {filepath}: {str(e)}")
        return filepath # Return original if processing fails

