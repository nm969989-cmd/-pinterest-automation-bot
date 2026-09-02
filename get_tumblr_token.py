"""
Tumblr OAuth 1.0a Token Generator
===================================
Run this once to get your Tumblr OAuth Access Token.

Steps:
  1. Run this script → copy the AUTH_URL printed
  2. Open the AUTH_URL in your browser and authorize
  3. Copy the ?oauth_verifier=XXX from the redirect URL
  4. Re-run: python get_tumblr_token.py --verifier <VERIFIER> --token <TEMP_TOKEN> --secret <TEMP_SECRET>
  5. Copy the final ACCESS_TOKEN + ACCESS_TOKEN_SECRET into your .env

Usage:
  python get_tumblr_token.py                        # Step 1: get auth URL
  python get_tumblr_token.py --verifier V --token T --secret S   # Step 2: get final token
"""

import time
import hmac
import hashlib
import base64
import urllib.parse
import sys
import os
import requests

# ─── ANSI Colors for terminal output ───────────────────────────────────────────
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_RESET  = "\033[0m"

def _ok(msg):   print(f"  {_GREEN}✅ {msg}{_RESET}")
def _warn(msg): print(f"  {_YELLOW}⚠️  {msg}{_RESET}")
def _err(msg):  print(f"  {_RED}❌ {msg}{_RESET}")
def _info(msg): print(f"  {_CYAN}ℹ️  {msg}{_RESET}")

# ─── Credentials (from .env or hardcoded below) ────────────────────────────────
CONSUMER_KEY    = os.getenv("TUMBLR_CONSUMER_KEY",    "jISRSZLLpKhxd2XWNrKYuZ7I9RLpwxAFqwksbkyOw15ZkQuDo")
CONSUMER_SECRET = os.getenv("TUMBLR_CONSUMER_SECRET", "C5dR8uw8i4cpogQNVxC0quGkc1TBVjAMsd4pBxQhuOjw1AxyFH")
CALLBACK_URL    = "https://in.pinterest.com/animeasthetic/"   # must match Tumblr app settings


def _nonce() -> str:
    """Generates a unique request nonce."""
    raw = base64.b64encode(f"{time.time()}{os.urandom(8).hex()}".encode()).decode()
    return raw.replace("=", "").replace("+", "").replace("/", "")[:32]


def generate_oauth_signature(url: str, method: str, params: dict,
                              consumer_secret: str, token_secret: str = "") -> str:
    """
    Generates an HMAC-SHA1 OAuth 1.0a signature.
    All parameters (query string + OAuth header) must be passed in `params`.
    """
    # 1. Percent-encode and sort all params
    sorted_params = sorted(params.items())
    param_str = urllib.parse.urlencode(sorted_params, quote_via=urllib.parse.quote)

    # 2. Build the base string
    base_string = (
        f"{method.upper()}"
        f"&{urllib.parse.quote(url, safe='')}"
        f"&{urllib.parse.quote(param_str, safe='')}"
    )

    # 3. Build the signing key
    signing_key = (
        f"{urllib.parse.quote(consumer_secret, safe='')}"
        f"&{urllib.parse.quote(token_secret, safe='')}"
    ).encode("utf-8")

    # 4. HMAC-SHA1
    signature = base64.b64encode(
        hmac.new(signing_key, base_string.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")

    return signature


def build_oauth_header(url: str, method: str, extra_params: dict,
                        consumer_secret: str, token_secret: str = "",
                        oauth_token: str = "") -> str:
    """
    Builds a complete OAuth Authorization header string.
    Returns the full header value (ready to pass as Authorization: <value>).
    """
    oauth_params = {
        "oauth_consumer_key":     CONSUMER_KEY,
        "oauth_nonce":            _nonce(),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        str(int(time.time())),
        "oauth_version":          "1.0",
    }
    if oauth_token:
        oauth_params["oauth_token"] = oauth_token

    # Merge all params for signature calculation
    all_params = {**oauth_params, **extra_params}
    oauth_params["oauth_signature"] = generate_oauth_signature(
        url, method, all_params, consumer_secret, token_secret
    )

    # Build the header (only oauth_ keys go in the Authorization header)
    parts = [
        f'{k}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
        if k.startswith("oauth_")
    ]
    return f"OAuth {', '.join(parts)}"


def step1_request_token() -> tuple[str, str]:
    """
    Step 1: Exchange consumer credentials for a temporary request token.
    Returns (oauth_token, oauth_token_secret).
    """
    print(f"\n{_BOLD}[Step 1] Requesting temporary OAuth token from Tumblr...{_RESET}")
    url = "https://www.tumblr.com/oauth/request_token"

    auth_header = build_oauth_header(
        url=url,
        method="POST",
        extra_params={"oauth_callback": CALLBACK_URL},
        consumer_secret=CONSUMER_SECRET,
    )
    headers = {"Authorization": auth_header}

    try:
        res = requests.post(url, headers=headers, timeout=15)
        res.raise_for_status()
    except requests.exceptions.HTTPError as e:
        _err(f"HTTP {res.status_code}: {res.text[:300]}")
        raise SystemExit(1) from e
    except requests.exceptions.RequestException as e:
        _err(f"Network error: {e}")
        raise SystemExit(1) from e

    data = urllib.parse.parse_qs(res.text)
    if "oauth_token" not in data or "oauth_token_secret" not in data:
        _err(f"Unexpected response from Tumblr:\n{res.text[:500]}")
        raise SystemExit(1)

    token        = data["oauth_token"][0]
    token_secret = data["oauth_token_secret"][0]
    confirmed    = data.get("oauth_callback_confirmed", ["false"])[0]

    _ok(f"Temporary token received (callback confirmed: {confirmed})")

    auth_url = f"https://www.tumblr.com/oauth/authorize?oauth_token={token}"
    print(f"\n{_BOLD}{_YELLOW}  ➤  OPEN THIS URL IN YOUR BROWSER AND AUTHORIZE:{_RESET}")
    print(f"\n     {_CYAN}{auth_url}{_RESET}\n")
    print("  After authorizing, Tumblr will redirect to your callback URL.")
    print("  Copy the  oauth_verifier=XXXX  value from the redirect URL.\n")
    print(f"  {_BOLD}Then run:{_RESET}")
    print(f"  python get_tumblr_token.py --verifier <VERIFIER> --token {token} --secret {token_secret}\n")

    return token, token_secret


def step2_access_token(oauth_token: str, oauth_token_secret: str, verifier: str) -> tuple[str, str]:
    """
    Step 2: Exchange the verifier for a permanent access token.
    Returns (access_token, access_token_secret).
    """
    print(f"\n{_BOLD}[Step 2] Exchanging verifier for permanent access token...{_RESET}")
    url = "https://www.tumblr.com/oauth/access_token"

    auth_header = build_oauth_header(
        url=url,
        method="POST",
        extra_params={"oauth_verifier": verifier},
        consumer_secret=CONSUMER_SECRET,
        token_secret=oauth_token_secret,
        oauth_token=oauth_token,
    )
    headers = {"Authorization": auth_header}

    try:
        res = requests.post(url, headers=headers, timeout=15)
        res.raise_for_status()
    except requests.exceptions.HTTPError as e:
        _err(f"HTTP {res.status_code}: {res.text[:300]}")
        raise SystemExit(1) from e
    except requests.exceptions.RequestException as e:
        _err(f"Network error: {e}")
        raise SystemExit(1) from e

    data = urllib.parse.parse_qs(res.text)
    if "oauth_token" not in data or "oauth_token_secret" not in data:
        _err(f"Unexpected response:\n{res.text[:500]}")
        raise SystemExit(1)

    access_token        = data["oauth_token"][0]
    access_token_secret = data["oauth_token_secret"][0]

    _ok("Permanent access token received!")

    print(f"\n{'═' * 55}")
    print(f"{_BOLD}{_GREEN}  ✅  SUCCESS — Add these to your .env / Render env vars:{_RESET}")
    print(f"{'═' * 55}")
    print(f"\n  TUMBLR_ACCESS_TOKEN        = {access_token}")
    print(f"  TUMBLR_ACCESS_TOKEN_SECRET = {access_token_secret}")
    print(f"\n{'═' * 55}\n")

    # Auto-write to .env if it exists in the current directory
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            env_content = f.read()

        lines_to_add = []
        if "TUMBLR_ACCESS_TOKEN=" not in env_content:
            lines_to_add.append(f"TUMBLR_ACCESS_TOKEN={access_token}")
        if "TUMBLR_ACCESS_TOKEN_SECRET=" not in env_content:
            lines_to_add.append(f"TUMBLR_ACCESS_TOKEN_SECRET={access_token_secret}")

        if lines_to_add:
            with open(env_file, "a") as f:
                f.write("\n# Tumblr OAuth Tokens (auto-generated)\n")
                f.write("\n".join(lines_to_add) + "\n")
            _ok(f"Tokens auto-saved to .env ✔")
        else:
            _warn(".env already contains TUMBLR_ACCESS_TOKEN entries — update manually if needed.")
    else:
        _info(".env file not found — copy the tokens above manually.")

    return access_token, access_token_secret


def _parse_args() -> dict:
    """Simple argument parser (no argparse dependency)."""
    args = {"verifier": None, "token": None, "secret": None}
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--verifier" and i + 1 < len(argv):
            args["verifier"] = argv[i + 1]; i += 2
        elif argv[i] == "--token" and i + 1 < len(argv):
            args["token"] = argv[i + 1]; i += 2
        elif argv[i] == "--secret" and i + 1 < len(argv):
            args["secret"] = argv[i + 1]; i += 2
        else:
            i += 1
    return args


if __name__ == "__main__":
    print(f"\n{'═' * 55}")
    print(f"{_BOLD}  🎌 Tumblr OAuth 1.0a Token Generator{_RESET}")
    print(f"{'═' * 55}")

    if not CONSUMER_KEY or not CONSUMER_SECRET:
        _err("CONSUMER_KEY and CONSUMER_SECRET must be set!")
        raise SystemExit(1)

    cli = _parse_args()

    if cli["verifier"] and cli["token"] and cli["secret"]:
        # Step 2: exchange verifier for access token
        step2_access_token(cli["token"], cli["secret"], cli["verifier"])
    else:
        # Step 1: get auth URL
        step1_request_token()
