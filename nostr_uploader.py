"""
nostr_uploader.py — Nostr (NIP-01) Cross-Posting Module
=======================================================
Publishes anime wallpapers + merch links to Nostr relays as signed kind-1 notes
with inline image URLs and hashtag (`t`) tags.

Zero extra dependencies: uses Python stdlib for BIP-340 Schnorr / Bech32 and
aiohttp (already in requirements.txt) for WebSocket relay publishing.

Usage:
    from nostr_uploader import post_to_nostr, verify_nostr_key
    ok, url = post_to_nostr(image_url="https://...", title="...", link="https://...")
"""

import os
import json
import time
import asyncio
import hashlib
import secrets
from typing import Tuple, List, Optional
import aiohttp
from logger import get_logger

logger = get_logger(__name__)

# ── secp256k1 / BIP-340 Schnorr Constants ────────────────────────────────────
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

_DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://nostr.mom",
    "wss://relay.snort.social",
]

# ── Bech32 Helpers (nsec / npub / note) ──────────────────────────────────────
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values: List[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> List[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _convertbits(data: bytes, frombits: int, tobits: int, pad: bool = True) -> Optional[List[int]]:
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def _bech32_encode(hrp: str, data_bytes: bytes) -> str:
    five_bit = _convertbits(data_bytes, 8, 5, True) or []
    combined = five_bit + [0, 0, 0, 0, 0, 0]
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + combined) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in (five_bit + checksum))


def _bech32_decode(bech: str) -> Tuple[Optional[str], Optional[bytes]]:
    bech = bech.strip().lower()
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        return None, None
    hrp = bech[:pos]
    data_part = bech[pos + 1:]
    try:
        vals = [_BECH32_CHARSET.index(c) for c in data_part]
    except ValueError:
        return None, None
    if _bech32_polymod(_bech32_hrp_expand(hrp) + vals) != 1:
        return None, None
    decoded = _convertbits(bytes(vals[:-6]), 5, 8, False)
    if decoded is None:
        return None, None
    return hrp, bytes(decoded)


# ── Elliptic Curve & BIP-340 Schnorr Signing ─────────────────────────────────
def _point_add(p1: Optional[Tuple[int, int]], p2: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and y1 != y2:
        return None
    if x1 == x2:
        lam = (3 * x1 * x1 * pow(2 * y1, _P - 2, _P)) % _P
    else:
        lam = ((y2 - y1) * pow((x2 - x1) % _P, _P - 2, _P)) % _P
    x3 = (lam * lam - x1 - x2) % _P
    y3 = (lam * (x1 - x3) - y1) % _P
    return (x3, y3)


def _point_mul(p: Tuple[int, int], n: int) -> Optional[Tuple[int, int]]:
    r = None
    cur = p
    while n > 0:
        if n & 1:
            r = _point_add(r, cur)
        cur = _point_add(cur, cur)
        n >>= 1
    return r


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode("utf-8")).digest()
    return hashlib.sha256(tag_hash + tag_hash + msg).digest()


def _schnorr_sign(msg32: bytes, seckey: bytes) -> bytes:
    d0 = int.from_bytes(seckey, "big")
    if not (1 <= d0 < _N):
        raise ValueError("Invalid secp256k1 private key")
    pt = _point_mul(_G, d0)
    assert pt is not None
    d = d0 if (pt[1] % 2 == 0) else (_N - d0)
    aux = secrets.token_bytes(32)
    t = (d ^ int.from_bytes(_tagged_hash("BIP0340/aux", aux), "big")).to_bytes(32, "big")
    k0 = int.from_bytes(_tagged_hash("BIP0340/nonce", t + pt[0].to_bytes(32, "big") + msg32), "big") % _N
    if k0 == 0:
        raise RuntimeError("Nonce generation failed")
    r_pt = _point_mul(_G, k0)
    assert r_pt is not None
    k = k0 if (r_pt[1] % 2 == 0) else (_N - k0)
    r_bytes = r_pt[0].to_bytes(32, "big")
    p_bytes = pt[0].to_bytes(32, "big")
    e = int.from_bytes(_tagged_hash("BIP0340/challenge", r_bytes + p_bytes + msg32), "big") % _N
    s = (k + e * d) % _N
    return r_bytes + s.to_bytes(32, "big")


def _parse_private_key(raw_key: str) -> Optional[bytes]:
    key = (raw_key or "").strip()
    if not key:
        return None
    if key.lower().startswith("nsec1"):
        hrp, data = _bech32_decode(key)
        if hrp == "nsec" and data and len(data) == 32:
            return data
        return None
    if len(key) == 64:
        try:
            return bytes.fromhex(key)
        except ValueError:
            return None
    return None


def _get_pubkey_hex(seckey: bytes) -> str:
    d0 = int.from_bytes(seckey, "big")
    pt = _point_mul(_G, d0)
    assert pt is not None
    return pt[0].to_bytes(32, "big").hex()


def verify_nostr_key() -> Tuple[bool, str]:
    """Validates configured NOSTR_PRIVATE_KEY and returns (ok, npub_or_error)."""
    from config import NOSTR_PRIVATE_KEY
    seckey = _parse_private_key(NOSTR_PRIVATE_KEY)
    if not seckey:
        return False, "Missing or invalid NOSTR_PRIVATE_KEY (expected nsec1... or 64-char hex)"
    try:
        pub_bytes = bytes.fromhex(_get_pubkey_hex(seckey))
        npub = _bech32_encode("npub", pub_bytes)
        return True, npub
    except Exception as e:
        return False, str(e)


async def _publish_to_relay(session: aiohttp.ClientSession, relay_url: str, event: dict) -> bool:
    try:
        async with session.ws_connect(relay_url, timeout=10) as ws:
            await ws.send_str(json.dumps(["EVENT", event]))
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if isinstance(data, list) and len(data) >= 3 and data[0] == "OK" and data[1] == event["id"]:
                        return bool(data[2])
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
    except Exception as e:
        logger.debug(f"[Nostr] Relay {relay_url} failed: {e}")
    return False


async def _broadcast_event(relays: List[str], event: dict) -> int:
    async with aiohttp.ClientSession() as session:
        tasks = [_publish_to_relay(session, r, event) for r in relays]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if r is True)


def post_to_nostr(
    image_url: str,
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
    link: str = "",
) -> Tuple[bool, str]:
    """
    Publishes a signed NIP-01 kind-1 note to Nostr relays with the image URL,
    caption, merch link, and hashtags.
    Returns (success: bool, note_url: str).
    """
    from config import NOSTR_ENABLED, NOSTR_PRIVATE_KEY, NOSTR_RELAYS

    if not NOSTR_ENABLED:
        logger.debug("[Nostr] NOSTR_ENABLED=false -- skipping.")
        return False, ""

    seckey = _parse_private_key(NOSTR_PRIVATE_KEY)
    if not seckey:
        logger.error("[Nostr] Invalid or missing NOSTR_PRIVATE_KEY.")
        return False, ""

    if not image_url or not image_url.startswith(("http://", "https://")):
        logger.warning("[Nostr] Valid public image_url required.")
        return False, ""

    clean_tags = [t.lstrip("#").lower() for t in (tags or ["anime", "animeart", "wallpaper", "4k"]) if t.strip()][:8]
    hashtag_line = " ".join(f"#{t}" for t in clean_tags)

    parts = []
    if title:
        parts.append(title.strip())
    if description and description.strip() != title.strip():
        parts.append(description.strip()[:280])
    if link:
        parts.append(f"4K Wallpaper & Merch: {link.strip()}")
    if hashtag_line:
        parts.append(hashtag_line)
    parts.append(image_url.strip())

    content = "\n\n".join(parts)
    pubkey_hex = _get_pubkey_hex(seckey)
    created_at = int(time.time())
    event_tags = [["t", t] for t in clean_tags]
    event_tags.append(["imeta", f"url {image_url.strip()}"])

    serialized = json.dumps(
        [0, pubkey_hex, created_at, 1, event_tags, content],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    event_id = hashlib.sha256(serialized).hexdigest()
    sig = _schnorr_sign(bytes.fromhex(event_id), seckey).hex()

    event = {
        "id": event_id,
        "pubkey": pubkey_hex,
        "created_at": created_at,
        "kind": 1,
        "tags": event_tags,
        "content": content,
        "sig": sig,
    }

    relays = [r.strip() for r in (NOSTR_RELAYS or "").split(",") if r.strip().startswith("wss://")] or _DEFAULT_RELAYS

    try:
        loop = asyncio.new_event_loop()
        try:
            ok_count = loop.run_until_complete(_broadcast_event(relays, event))
        finally:
            loop.close()
    except Exception as e:
        logger.warning(f"[Nostr] Relay broadcast error: {e}")
        return False, ""

    if ok_count > 0:
        note_id = _bech32_encode("note", bytes.fromhex(event_id))
        note_url = f"https://njump.me/{note_id}"
        logger.info(f"[Nostr] Published to {ok_count}/{len(relays)} relays: {note_url}")
        return True, note_url

    logger.warning(f"[Nostr] Failed to publish to any relay ({len(relays)} tried).")
    return False, ""
