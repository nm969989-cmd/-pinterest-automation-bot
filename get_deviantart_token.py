"""
get_deviantart_token.py — DeviantArt OAuth 2.0 PKCE Token Generator
===================================================================
Generates fresh DeviantArt Access Token & Refresh Token using
PKCE authorization with https://localhost redirect.

Persists rotated tokens across:
  1. Local .env file
  2. Local SQLite database (bot_metadata)
  3. JSONBin.io cloud backup (so Render instance restores it on restart)

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

CLIENT_ID     = os.getenv("DEVIANTART_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("DEVIANTART_CLIENT_SECRET", "").strip()
REDIRECT_URI  = "https://localhost"


def _generate_pkce():
    """Generates PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def update_env_file(access_token: str, refresh_token: str = ""):
    """Updates DEVIANTART_ACCESS_TOKEN and DEVIANTART_REFRESH_TOKEN in .env."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        print(f"[WARN] .env not found at {env_path}")
        return False
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        found_acc = False
        found_ref = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("DEVIANTART_ACCESS_TOKEN="):
                new_lines.append(f"DEVIANTART_ACCESS_TOKEN={access_token}\n")
                found_acc = True
            elif refresh_token and line.strip().startswith("DEVIANTART_REFRESH_TOKEN="):
                new_lines.append(f"DEVIANTART_REFRESH_TOKEN={refresh_token}\n")
                found_ref = True
            else:
                new_lines.append(line)

        if not found_acc:
            new_lines.append(f"DEVIANTART_ACCESS_TOKEN={access_token}\n")
        if refresh_token and not found_ref:
            new_lines.append(f"DEVIANTART_REFRESH_TOKEN={refresh_token}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        print("[OK] Updated .env with new DeviantArt tokens.")
        return True
    except Exception as e:
        print(f"[WARN] Failed to write to .env: {e}")
        return False


def main():
    print("\n" + "=" * 65)
    print("  [DeviantArt OAuth 2.0 PKCE Token Generator]")
    print("=" * 65)

    if not CLIENT_ID or not CLIENT_SECRET:
        print("[ERROR] DEVIANTART_CLIENT_ID or DEVIANTART_CLIENT_SECRET is missing in .env!")
        sys.exit(1)

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

    print("\nStep 1: Open this authorization URL in your browser:\n")
    print(auth_url)
    print("\n(Attempting to open default browser automatically...)")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("\nStep 2: Log into DeviantArt and click 'Authorize'.")
    print("        DeviantArt will redirect your browser to an address like:")
    print("        https://localhost/?code=XXXXXXXXXXXXXXXXXXXX\n")
    print("        (The browser will say 'This site can't be reached' — this is normal!)")
    print("        Just copy the FULL URL from the browser's address bar.\n")

    user_input = input("Paste the redirected URL (or code) here: ").strip()

    code = user_input
    if "code=" in user_input:
        parsed = urllib.parse.urlparse(user_input)
        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [user_input])[0]

    if not code:
        print("\n[ERROR] No authorization code provided!")
        sys.exit(1)

    print("\nExchanging authorization code for fresh access & refresh tokens...")
    token_url = "https://www.deviantart.com/oauth2/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
        "code_verifier": verifier,
    }

    try:
        resp = requests.post(token_url, data=data, timeout=25)
    except Exception as req_err:
        print(f"\n[ERROR] Network request failed: {req_err}")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"\n[ERROR] Token exchange failed (HTTP {resp.status_code}): {resp.text}")
        sys.exit(1)

    t_data = resp.json()
    acc = t_data.get("access_token", "")
    ref = t_data.get("refresh_token", "")

    if not acc:
        print(f"\n[ERROR] No access_token returned in response: {t_data}")
        sys.exit(1)

    # Verify user profile with newly generated access token
    username = "unknown"
    try:
        who_res = requests.get(
            "https://www.deviantart.com/api/v1/oauth2/user/whoami",
            headers={"Authorization": f"Bearer {acc}"},
            timeout=10,
        )
        if who_res.status_code == 200:
            username = who_res.json().get("username", "unknown")
            print(f"\n[SUCCESS] Successfully authenticated as @{username} on DeviantArt!")
        else:
            print(f"\n[WARN] Token verification returned HTTP {who_res.status_code}")
    except Exception as e:
        print(f"\n[WARN] Profile check failed: {e}")

    # 1. Update local .env
    update_env_file(acc, ref)

    # 2. Persist in SQLite
    try:
        from database import set_metadata
        set_metadata("deviantart_access_token", acc)
        if ref:
            set_metadata("deviantart_refresh_token", ref)
        print("[OK] Persisted tokens in local database (bot_metadata).")
    except Exception as e:
        print(f"[WARN] Database persistence failed: {e}")

    # 3. Push immediate backup to JSONBin so Render has the token
    try:
        import jsonbin_sync
        jsonbin_sync.trigger_cloud_sync_now(min_interval_sec=0)
        print("[OK] Synchronized updated tokens to JSONBin cloud backup.")
    except Exception as sync_e:
        print(f"[WARN] Cloud backup sync failed: {sync_e}")

    print("\n" + "=" * 65)
    print(" [SUCCESS] DeviantArt is now 100% active and authenticated!")
    print("=" * 65)


if __name__ == "__main__":
    main()
