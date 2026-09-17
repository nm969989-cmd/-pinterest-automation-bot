"""
get_deviantart_token.py — DeviantArt OAuth 2.0 Token Generator
==============================================================
Runs a temporary local webserver to authenticate with DeviantArt
and obtain your initial Access Token & Refresh Token.

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
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("DEVIANTART_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DEVIANTART_CLIENT_SECRET", "")
REDIRECT_URI  = "https://localhost"
AUTH_CODE     = None


def _generate_pkce():
    """Generates PKCE code_verifier and code_challenge (S256) for OAuth 2.1."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global AUTH_CODE
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if "code" in query:
            AUTH_CODE = query["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html>
                <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                    <h2 style="color: #05cc47;">Authorization Successful!</h2>
                    <p>You can close this tab and return to your terminal.</p>
                </body>
                </html>
            """)
        else:
            error = query.get("error_description", ["Unknown error"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h2>Authorization Failed: {error}</h2>".encode())

    def log_message(self, format, *args):
        pass  # Suppress default server access logs


def main():
    global CLIENT_ID, CLIENT_SECRET

    print("\n" + "=" * 60)
    print(" DeviantArt OAuth 2.0 Token Generator")
    print("=" * 60)

    if not CLIENT_ID or not CLIENT_SECRET:
        CLIENT_ID = input("Enter your DeviantArt CLIENT ID: ").strip()
        CLIENT_SECRET = input("Enter your DeviantArt CLIENT SECRET: ").strip()

    if not CLIENT_ID or not CLIENT_SECRET:
        print("[ERROR] Client ID and Client Secret are required!")
        sys.exit(1)

    # Scopes needed for uploading and publishing art
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
    print("   DeviantArt will redirect to a URL like: https://localhost/?code=XXXXX")
    print("   (Even if the page says 'This site can't be reached', that is normal!)\n")
    
    user_input = input("Paste the FULL redirected URL (or just the code): ").strip()
    
    code = user_input
    if "code=" in user_input:
        parsed = urllib.parse.urlparse(user_input)
        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [user_input])[0]

    if not code:
        print("[ERROR] No authorization code provided!")
        sys.exit(1)

    print(f"\n[OK] Received code! Exchanging for tokens...")

    # Exchange code for access & refresh token
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
        # If OAuth 2.0 without PKCE
        data.pop("code_verifier", None)
        resp = requests.post(token_url, data=data)

    if resp.status_code != 200:
        print(f"[ERROR] Token exchange failed: {resp.status_code} - {resp.text}")
        sys.exit(1)

    token_data = resp.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    username = token_data.get("username", "")

    print("\n" + "=" * 60)
    print(f" SUCCESS! Connected DeviantArt account: {username}")
    print("=" * 60)
    print(f"DEVIANTART_CLIENT_ID={CLIENT_ID}")
    print(f"DEVIANTART_CLIENT_SECRET={CLIENT_SECRET}")
    print(f"DEVIANTART_ACCESS_TOKEN={access_token}")
    print(f"DEVIANTART_REFRESH_TOKEN={refresh_token}")
    print("=" * 60)

    # Optionally append or update .env
    update_env = input("\nSave these directly into your .env file? (y/n): ").strip().lower()
    if update_env in ["y", "yes", ""]:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        keys_to_set = {
            "DEVIANTART_CLIENT_ID": CLIENT_ID,
            "DEVIANTART_CLIENT_SECRET": CLIENT_SECRET,
            "DEVIANTART_ACCESS_TOKEN": access_token,
            "DEVIANTART_REFRESH_TOKEN": refresh_token,
            "DEVIANTART_ENABLED": "true"
        }

        new_lines = []
        handled = set()
        for line in lines:
            key = line.split("=")[0].strip()
            if key in keys_to_set:
                new_lines.append(f"{key}={keys_to_set[key]}\n")
                handled.add(key)
            else:
                new_lines.append(line)

        for key, val in keys_to_set.items():
            if key not in handled:
                new_lines.append(f"{key}={val}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print("[OK] Saved into .env successfully!")


if __name__ == "__main__":
    main()
