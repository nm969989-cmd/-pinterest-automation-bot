"""
get_deviantart_token.py — DeviantArt OAuth 2.0 PKCE Token Generator
===================================================================
Generates fresh DeviantArt Access Token & Refresh Token using
PKCE authorization with https://localhost redirect.

Usage:
    python get_deviantart_token.py
"""

import os
import sys
import secrets
import hashlib
import base64
import webbrowser
import urllib.parse
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("DEVIANTART_CLIENT_ID", "77484")
CLIENT_SECRET = os.getenv("DEVIANTART_CLIENT_SECRET", "089b87f80b4640d6fe58a8275ca68e71")
REDIRECT_URI  = "https://localhost"


def _generate_pkce():
    """Generates PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def main():
    print("\n" + "=" * 60)
    print(" DeviantArt OAuth 2.0 PKCE Token Generator")
    print("=" * 60)

    verifier, challenge = _generate_pkce()
    scopes = "stash publish browse"
    auth_params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": scopes,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"https://www.deviantart.com/oauth2/authorize?{urllib.parse.urlencode(auth_params)}"

    print(f"\n1. Opening browser for authorization:\n{auth_url}\n")
    webbrowser.open(auth_url)

    print("2. Click 'Authorize' in your browser.")
    print("   DeviantArt will redirect to: https://localhost/?code=XXXXX\n")
    
    user_input = input("Paste the redirected URL (or code): ").strip()
    
    code = user_input
    if "code=" in user_input:
        parsed = urllib.parse.urlparse(user_input)
        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [user_input])[0]

    if not code:
        print("[ERROR] No code provided!")
        sys.exit(1)

    print("\nExchanging code for tokens...")
    token_url = "https://www.deviantart.com/oauth2/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
        "code_verifier": verifier,
    }

    resp = requests.post(token_url, data=data)
    if resp.status_code != 200:
        print(f"[ERROR] Token exchange failed: {resp.status_code} - {resp.text}")
        sys.exit(1)

    t_data = resp.json()
    acc = t_data.get("access_token", "")
    ref = t_data.get("refresh_token", "")
    print("\nSUCCESS!")
    print(f"DEVIANTART_ACCESS_TOKEN={acc}")
    print(f"DEVIANTART_REFRESH_TOKEN={ref}")

    # Persist in sqlite
    try:
        from database import set_metadata
        set_metadata("deviantart_access_token", acc)
        set_metadata("deviantart_refresh_token", ref)
        print("[OK] Persisted tokens in local database.")
    except Exception as e:
        print(f"[WARN] Database persistence failed: {e}")


if __name__ == "__main__":
    main()

