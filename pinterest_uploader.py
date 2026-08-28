import os
import time
import requests
from config import PINTEREST_ACCESS_TOKEN, PINTEREST_BOARD_ID, DRY_RUN
from logger import get_logger
from database import is_file_uploaded, mark_file_uploaded

logger = get_logger(__name__)

# Duplicate uploads are now tracked in SQLite (see database.py)
# Persistent across restarts — duplicates are prevented even after a crash.

def get_board_id_dynamically(headers):
    """Fetches the board ID dynamically using the API if the user didn't provide it"""
    try:
        url = "https://api.pinterest.com/v5/boards"
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            boards = res.json().get('items', [])
            for b in boards:
                # Default to the first board if we can't match it perfectly, or match by name
                if "anime" in b.get('name', '').lower() or not PINTEREST_BOARD_ID:
                    return b.get('id')
            if boards:
                return boards[0].get('id') # Fallback to first board
    except Exception as e:
        logger.error(f"Failed to fetch board ID: {e}")
    return PINTEREST_BOARD_ID

def upload_to_pinterest(image_path, title, description, link):
    """
    Uploads a pin to Pinterest using API v5.
    When DRY_RUN=true in .env, logs the pin data instead of uploading.
    """
    filename = os.path.basename(image_path)

    # ── Duplicate check ───────────────────────────────────────────────────────
    if is_file_uploaded(filename):
        logger.info(f"Duplicate detected (persistent), skipping: {filename}")
        return False

    # ── Dry Run Mode ──────────────────────────────────────────────────────────
    if DRY_RUN:
        logger.info("[DRY RUN] ----------------------------------------")
        logger.info("[DRY RUN] Would upload pin:")
        logger.info(f"[DRY RUN]   Image    : {image_path}")
        logger.info(f"[DRY RUN]   Title    : {title}")
        logger.info(f"[DRY RUN]   Link     : {link}")
        logger.info(f"[DRY RUN]   Desc     : {description[:100]}...")
        logger.info("[DRY RUN] ----------------------------------------")
        mark_file_uploaded(filename, title)  # Still track so no duplicates
        return True  # Return success so scheduler doesn't re-queue

    if not PINTEREST_ACCESS_TOKEN:
        logger.error("Pinterest credentials missing.")
        return False

    try:
        logger.info(f"Uploading pin: '{title}'")

        # Pinterest API v5 endpoint for creating pins
        url = "https://api.pinterest.com/v5/pins"
        
        headers = {
            "Authorization": f"Bearer {PINTEREST_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        actual_board_id = PINTEREST_BOARD_ID
        if not actual_board_id or not actual_board_id.isdigit():
            actual_board_id = get_board_id_dynamically(headers)
            if not actual_board_id:
                logger.error("Could not determine Board ID.")
                return False
        
        # Step 1: We need to host the image somewhere public, or upload it as media.
        # Pinterest API v5 typically requires the image to be publicly accessible via URL
        # OR uploaded via their media upload endpoint first.
        # Since we are downloading from Telegram to local disk, we must use the media endpoint.
        
        # --- Media Upload Process ---
        # 1. Register media upload
        media_url = "https://api.pinterest.com/v5/media"
        media_data = {"media_type": "image"}
        media_res = requests.post(media_url, headers=headers, json=media_data)
        
        if media_res.status_code not in (200, 201):
            logger.error(f"Failed to register media: {media_res.text}")
            return False
            
        media_info = media_res.json()
        upload_id = media_info.get("media_id")
        upload_url = media_info.get("upload_url")
        upload_params = media_info.get("upload_parameters", {})
        
        # 2. Upload file to AWS S3 (via the pre-signed URL provided by Pinterest)
        with open(image_path, 'rb') as f:
            files = {'file': f}
            # upload_params contains necessary S3 form fields
            s3_res = requests.post(upload_url, data=upload_params, files=files)
            
        if s3_res.status_code not in (200, 204):
            logger.error(f"Failed to upload to S3: {s3_res.text}")
            return False
            
        # 3. Wait for processing (can take a few seconds)
        status_url = f"{media_url}/{upload_id}"
        max_retries = 5
        media_ready = False
        
        for _ in range(max_retries):
            status_res = requests.get(status_url, headers=headers)
            if status_res.status_code == 200:
                status = status_res.json().get("status")
                if status == "succeeded":
                    media_ready = True
                    break
                elif status == "failed":
                    logger.error("Media processing failed by Pinterest.")
                    return False
            time.sleep(2)
            
        if not media_ready:
            logger.error("Media processing timed out.")
            return False
            
        # --- Pin Creation ---
        pin_data = {
            "board_id": actual_board_id,
            "media_source": {
                "source_type": "media_id",
                "media_id": upload_id
            },
            "title": title[:100], # Max 100 chars
            "description": description[:500], # Max 500 chars
            "link": link
        }
        
        res = requests.post(url, headers=headers, json=pin_data)
        
        if res.status_code in (200, 201):
            logger.info(f"Successfully uploaded pin: {title}")
            mark_file_uploaded(filename, title)  # Persist to SQLite
            return True
        else:
            logger.error(f"Failed to create pin: {res.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error in Pinterest upload: {str(e)}")
        return False
