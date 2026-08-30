"""
pa_api.py — Amazon Product Advertising API v5 (India) integration.

Uses AWS Signature V4 signing (no extra libraries needed — pure requests).
Provides reliable ASIN lookup and product data as a replacement for
the fragile HTML scraper in amazon_search.py.

Setup:
  1. Go to affiliate-program.amazon.in → Tools → Product Advertising API
  2. Click "Manage Your Credentials" → Create new Access Key
  3. Copy Access Key ID, Secret Access Key into your .env file:
       AMAZON_PA_API_KEY=your_access_key_id
       AMAZON_PA_API_SECRET=your_secret_access_key
       AMAZON_PA_API_TAG=aniflexindia-21   (your affiliate tag)

The module auto-detects whether credentials are set and falls back to
the HTML scraper gracefully if they are not.
"""

import os
import hmac
import hashlib
import json
import datetime
import requests
from logger import get_logger

logger = get_logger(__name__)

# ── PA-API v5 India Configuration ────────────────────────────────────────────
_PA_API_HOST      = "webservices.amazon.in"
_PA_API_REGION    = "eu-west-1"          # India marketplace uses eu-west-1
_PA_API_SERVICE   = "ProductAdvertisingAPI"
_SEARCH_ENDPOINT  = "/paapi5/searchitems"
_GETITEMS_ENDPOINT = "/paapi5/getitems"

# ── Credentials (read from .env) ─────────────────────────────────────────────
_ACCESS_KEY = os.getenv("AMAZON_PA_API_KEY", "").strip()
_SECRET_KEY = os.getenv("AMAZON_PA_API_SECRET", "").strip()
_PARTNER_TAG = os.getenv("AMAZON_PA_API_TAG", os.getenv("AMAZON_AFFILIATE_TAG", "")).strip()

_CREDS_AVAILABLE = bool(_ACCESS_KEY and _SECRET_KEY and _PARTNER_TAG)

if _CREDS_AVAILABLE:
    logger.info("[PA-API] Amazon PA-API credentials loaded. Using official API for product lookup.")
else:
    logger.info("[PA-API] No PA-API credentials set. Will use HTML scraper fallback.")


# ── AWS Signature V4 Signing ──────────────────────────────────────────────────

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str) -> bytes:
    k_date    = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region  = _sign(k_date, _PA_API_REGION)
    k_service = _sign(k_region, _PA_API_SERVICE)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


def _build_signed_request(endpoint: str, payload: dict) -> tuple[str, dict]:
    """
    Builds a properly AWS Signature V4 signed HTTP request for PA-API v5.
    Returns (url, headers) tuple ready to POST with requests.
    """
    now = datetime.datetime.utcnow()
    amz_date   = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    body = json.dumps(payload, separators=(",", ":"))
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    canonical_uri     = endpoint
    canonical_query   = ""
    canonical_headers = (
        f"content-encoding:amz-1.0\n"
        f"content-type:application/json; charset=UTF-8\n"
        f"host:{_PA_API_HOST}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-target:com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{_op_name(endpoint)}\n"
    )
    signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"

    canonical_request = "\n".join([
        "POST",
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_headers,
        body_hash,
    ])

    credential_scope = f"{date_stamp}/{_PA_API_REGION}/{_PA_API_SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _get_signature_key(_SECRET_KEY, date_stamp)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = (
        f"AWS4-HMAC-SHA256 "
        f"Credential={_ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    headers = {
        "Content-Encoding":  "amz-1.0",
        "Content-Type":      "application/json; charset=UTF-8",
        "Host":              _PA_API_HOST,
        "X-Amz-Date":       amz_date,
        "X-Amz-Target":     f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{_op_name(endpoint)}",
        "Authorization":    auth_header,
        "User-Agent":       "AnimeBot/1.0 (Pinterest Affiliate Bot)",
    }

    url = f"https://{_PA_API_HOST}{endpoint}"
    return url, headers, body


def _op_name(endpoint: str) -> str:
    """Map endpoint path to PA-API operation name for X-Amz-Target header."""
    mapping = {
        _SEARCH_ENDPOINT:   "SearchItems",
        _GETITEMS_ENDPOINT: "GetItems",
    }
    return mapping.get(endpoint, "SearchItems")


# ── Public API Functions ───────────────────────────────────────────────────────

def search_items(keywords: str, search_index: str = "All",
                 item_count: int = 3) -> list[dict]:
    """
    Search Amazon India for products matching the keywords.
    Returns list of product dicts: {asin, title, url, price, image_url}

    Falls back to empty list if credentials not set or API errors.

    Args:
        keywords:     Search query (e.g. "Demon Slayer Tanjiro poster")
        search_index: Amazon category ("All", "Books", "Apparel", "Toys")
        item_count:   Max results to return (1-10)
    """
    if not _CREDS_AVAILABLE:
        return []

    payload = {
        "Keywords":     keywords,
        "PartnerTag":   _PARTNER_TAG,
        "PartnerType":  "Associates",
        "Marketplace":  "www.amazon.in",
        "SearchIndex":  search_index,
        "ItemCount":    item_count,
        "Resources": [
            "ItemInfo.Title",
            "Offers.Listings.Price",
            "Offers.Listings.Availability.Type",   # In-Stock Safety Filter
            "Offers.Listings.Availability.Message",
            "Images.Primary.Medium",
            "ItemInfo.ExternalIds",
        ],
    }

    try:
        url, headers, body = _build_signed_request(_SEARCH_ENDPOINT, payload)
        res = requests.post(url, headers=headers, data=body, timeout=12)

        if res.status_code != 200:
            logger.error(
                f"[PA-API] SearchItems HTTP {res.status_code}: "
                f"{res.text[:200]}"
            )
            return []

        data = res.json()
        items = data.get("SearchResult", {}).get("Items", [])
        results = []
        for item in items:
            asin  = item.get("ASIN", "")
            title = item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", "")
            listing = (
                item.get("Offers", {})
                    .get("Listings", [{}])
            )
            first_listing = listing[0] if listing else {}
            price_info   = first_listing.get("Price", {})
            avail_info   = first_listing.get("Availability", {})
            price        = price_info.get("DisplayAmount", "")
            avail_type   = avail_info.get("Type", "UNKNOWN")

            # ── In-Stock Safety Filter ─────────────────────────────────────────
            # Accept items that are available NOW or TOMORROW.
            # Reject: OUT_OF_STOCK, UNDELIVERABLE, or items with no price.
            IN_STOCK_TYPES = {"NOW", "TOMORROW", "DAYS_2_3", "WEEK", "AVAILABLE"}
            is_in_stock = (
                bool(price) and
                (avail_type in IN_STOCK_TYPES or avail_type == "UNKNOWN")
            )
            if not is_in_stock:
                logger.info(
                    f"[PA-API] Skipping out-of-stock item: ASIN={asin} "
                    f"({avail_type}, price='{price}')"
                )
                continue

            img = (
                item.get("Images", {})
                    .get("Primary", {})
                    .get("Medium", {})
                    .get("URL", "")
            )
            if asin:
                results.append({
                    "asin":      asin,
                    "title":     title,
                    "price":     price,
                    "image_url": img,
                    "in_stock":  True,
                    "url":       f"https://www.amazon.in/dp/{asin}?tag={_PARTNER_TAG}&linkCode=ogi&th=1&psc=1",
                })
        logger.info(
            f"[PA-API] SearchItems '{keywords}': "
            f"{len(results)} in-stock (of {len(items)} total results)"
        )
        return results

    except Exception as e:
        logger.error(f"[PA-API] SearchItems error: {e}")
        return []


def get_best_asin(keywords: str) -> str | None:
    """
    Returns the ASIN of the best matching product for the keywords,
    or None if not found / PA-API not configured.
    This is the main function called by amazon_search.py.
    """
    results = search_items(keywords, item_count=1)
    if results:
        asin = results[0]["asin"]
        title = results[0]["title"]
        logger.info(f"[PA-API] Best ASIN for '{keywords}': {asin} ({title[:50]})")
        return asin
    return None


def is_available() -> bool:
    """Returns True if PA-API credentials are configured and ready to use."""
    return _CREDS_AVAILABLE
