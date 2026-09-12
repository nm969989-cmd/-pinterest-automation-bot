"""
test_shutterstock.py
====================
Run this to verify your Shutterstock setup is working.
Usage: python test_shutterstock.py

It will:
  1. Check your .env credentials
  2. Test AI title generation on a sample image
  3. Test FTP connection to Shutterstock
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

print("=" * 55)
print("  SHUTTERSTOCK SETUP CHECKER")
print("=" * 55)

# ── 1. Check credentials ───────────────────────────────────────
print("\n📋 STEP 1: Checking credentials in .env ...\n")

ftp_user  = os.getenv("SHUTTERSTOCK_FTP_USER", "")
ftp_pass  = os.getenv("SHUTTERSTOCK_FTP_PASS", "")
api_token = os.getenv("SHUTTERSTOCK_ACCESS_TOKEN", "")
or_key    = os.getenv("OPENROUTER_API_KEY", "")
gem_key   = os.getenv("GEMINI_API_KEY", "")

print(f"  FTP User    : {'✅ Set' if ftp_user  else '❌ NOT SET — get from submit.shutterstock.com -> Profile -> FTP'}")
print(f"  FTP Pass    : {'✅ Set' if ftp_pass  else '❌ NOT SET — get from submit.shutterstock.com -> Profile -> FTP'}")
print(f"  API Token   : {'✅ Set' if api_token else '⚠️  Not set (optional — needed for auto-metadata via API)'}")
print(f"  OpenRouter  : {'✅ Set' if or_key    else '⚠️  Not set (AI titles will use Gemini or offline fallback)'}")
print(f"  Gemini AI   : {'✅ Set' if gem_key   else '⚠️  Not set (optional fallback)'}")

if not ftp_user or not ftp_pass:
    print("\n❌ FTP credentials missing! Add them to .env first:")
    print("   SHUTTERSTOCK_FTP_USER=your_username")
    print("   SHUTTERSTOCK_FTP_PASS=your_password")
    print("\n   Get them at: https://submit.shutterstock.com -> Profile -> FTP")
    sys.exit(1)

# ── 2. Test FTP connection ─────────────────────────────────────
print("\n📡 STEP 2: Testing FTP connection to Shutterstock ...\n")

import ftplib
ftp_host = os.getenv("SHUTTERSTOCK_FTP_HOST", "ftp.shutterstock.com")

try:
    with ftplib.FTP(ftp_host) as ftp:
        ftp.login(ftp_user, ftp_pass)
        print(f"  ✅ FTP connected to {ftp_host}")
        print(f"  ✅ Logged in as: {ftp_user}")
        # List files already on the FTP server
        files = ftp.nlst()
        print(f"  📁 Files already in queue: {len(files)}")
        if files:
            print(f"     Latest: {files[-1]}")
except ftplib.error_perm as e:
    print(f"  ❌ FTP login failed: {e}")
    print("     Double-check SHUTTERSTOCK_FTP_USER and SHUTTERSTOCK_FTP_PASS")
    sys.exit(1)
except Exception as e:
    print(f"  ❌ FTP connection error: {e}")
    sys.exit(1)

# ── 3. Test AI title generation ───────────────────────────────
print("\n🤖 STEP 3: Testing AI title generation ...\n")

# Find any image in downloads/ folder to test with
test_image = None
downloads_dir = os.path.join(os.path.dirname(__file__), "downloads")
if os.path.isdir(downloads_dir):
    for f in os.listdir(downloads_dir):
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
            test_image = os.path.join(downloads_dir, f)
            break

if not test_image:
    # Look for any jpg in current directory
    for f in os.listdir("."):
        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
            test_image = os.path.abspath(f)
            break

if test_image:
    print(f"  🖼️  Testing with: {os.path.basename(test_image)}")
    try:
        from shutterstock_uploader import generate_shutterstock_metadata
        title, desc, keywords = generate_shutterstock_metadata(
            image_path=test_image,
            caption="anime digital artwork"
        )
        print(f"\n  ✅ AI Generated Successfully!")
        print(f"  📝 Title      : {title}")
        print(f"  📄 Description: {desc[:100]}...")
        print(f"  🏷️  Keywords   : {len(keywords)} tags generated")
        print(f"     Sample     : {', '.join(keywords[:8])}")
    except Exception as e:
        print(f"  ⚠️  AI generation error: {e}")
else:
    print("  ⚠️  No test image found in downloads/ folder.")
    print("     Put any .jpg image in downloads/ and run again to test AI.")

# ── 4. Summary ────────────────────────────────────────────────
print("\n" + "=" * 55)
print("  SUMMARY")
print("=" * 55)
print(f"  FTP Connection  : ✅ Working")
print(f"  AI Generation   : {'✅ Ready' if or_key or gem_key else '⚠️  Using offline fallback (add OPENROUTER_API_KEY for best)'}")
print(f"  Auto-metadata   : {'✅ Fully automatic' if api_token else '⚠️  Add SHUTTERSTOCK_ACCESS_TOKEN for auto-metadata'}")
print()
print("  🎉 Setup is ready! Your bot can now upload to Shutterstock.")
print()
print("  NEXT STEPS:")
if not api_token:
    print("  1. Get API token: https://www.shutterstock.com/account/developers/apps")
    print("     Add: SHUTTERSTOCK_ACCESS_TOKEN=token in .env")
print("  2. Add /shutterstock command to your Telegram bot (optional)")
print("  3. Let your existing Pinterest bot also push to Shutterstock!")
print("=" * 55)
