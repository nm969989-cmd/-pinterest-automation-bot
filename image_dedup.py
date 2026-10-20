"""
image_dedup.py
==============
Visual Perceptual Hashing (dHash, aHash) and Cryptographic (SHA-256)
Deduplication Engine for the Pinterest Automation Bot.

Provides zero-dependency (pure Pillow) visual fingerprinting that detects:
1. Exact byte duplicates (via SHA-256).
2. Visual duplicates even if recompressed, resized, or saved in different formats (via 64-bit dHash).
"""

import os
import hashlib
from PIL import Image
from logger import get_logger

logger = get_logger(__name__)

# Standard Hamming distance threshold for 64-bit hashes.
# <= 4 bits difference = visually identical image (different compression/resolution).
# 5 to 10 bits = very similar (e.g. minor cropping or text overlay).
# > 10 bits = distinctly different image.
DEFAULT_HAMMING_THRESHOLD = 4


def compute_file_sha256(filepath: str) -> str:
    """Computes SHA-256 hash of a file's raw binary contents."""
    if not filepath or not os.path.isfile(filepath):
        return ""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.warning(f"[Dedup] Error computing SHA-256 for {filepath}: {e}")
        return ""


def compute_image_dhash(filepath: str, hash_size: int = 8) -> str:
    """
    Computes a 64-bit Difference Hash (dHash) for an image using Pillow.
    
    Algorithm:
    1. Convert to grayscale.
    2. Resize to (hash_size + 1, hash_size) -> (9x8) without preserving aspect ratio.
    3. Compare adjacent pixels horizontally (pixel[x] > pixel[x+1]).
    4. Convert 64 boolean comparisons to a 16-character hexadecimal string.
    
    Properties:
    - Highly resilient to JPEG compression, minor brightness changes, and scaling.
    - Extremely fast (pure Pillow, <5ms per image).
    """
    if not filepath or not os.path.isfile(filepath):
        return ""
    try:
        with Image.open(filepath) as img:
            # Convert to grayscale and resize to 9x8
            gray = img.convert("L").resize(
                (hash_size + 1, hash_size),
                Image.Resampling.BILINEAR
            )
            pixels = list(gray.getdata())
            
            # Compare adjacent pixels row by row
            diff = []
            width = hash_size + 1
            for row in range(hash_size):
                row_start = row * width
                for col in range(hash_size):
                    left = pixels[row_start + col]
                    right = pixels[row_start + col + 1]
                    diff.append(1 if left > right else 0)
            
            # Pack 64 bits into a 16-character hex string
            val = 0
            for bit in diff:
                val = (val << 1) | bit
            return f"{val:016x}"
    except Exception as e:
        logger.warning(f"[Dedup] Error computing dHash for {filepath}: {e}")
        return ""


def compute_image_ahash(filepath: str, hash_size: int = 8) -> str:
    """
    Computes a 64-bit Average Hash (aHash) for an image.
    
    Algorithm:
    1. Convert to grayscale and resize to 8x8.
    2. Calculate average pixel luminance.
    3. Each bit is 1 if pixel >= average, else 0.
    """
    if not filepath or not os.path.isfile(filepath):
        return ""
    try:
        with Image.open(filepath) as img:
            gray = img.convert("L").resize((hash_size, hash_size), Image.Resampling.BILINEAR)
            pixels = list(gray.getdata())
            avg = sum(pixels) / len(pixels)
            val = 0
            for p in pixels:
                val = (val << 1) | (1 if p >= avg else 0)
            return f"{val:016x}"
    except Exception as e:
        logger.warning(f"[Dedup] Error computing aHash for {filepath}: {e}")
        return ""


def hamming_distance(hash1: str, hash2: str) -> int:
    """
    Calculates the bitwise Hamming distance between two 16-char hex hashes.
    Returns number of differing bits (0 to 64). Returns 999 on invalid inputs.
    """
    if not hash1 or not hash2 or len(hash1) != 16 or len(hash2) != 16:
        return 999
    try:
        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        return bin(val1 ^ val2).count("1")
    except ValueError:
        return 999


def is_visually_identical(hash1: str, hash2: str, max_distance: int = DEFAULT_HAMMING_THRESHOLD) -> bool:
    """Returns True if the Hamming distance between two hashes is within the threshold."""
    dist = hamming_distance(hash1, hash2)
    return dist <= max_distance


def get_image_fingerprints(filepath: str) -> tuple[str, str]:
    """
    Convenience function returning (sha256, dhash) for an image file.
    Guarantees non-empty strings or empty strings on failure.
    """
    if not filepath or not os.path.isfile(filepath):
        return "", ""
    sha = compute_file_sha256(filepath)
    phash = compute_image_dhash(filepath)
    return sha, phash
