#!/usr/bin/env python3
"""
Upstox Token Converter
----------------------
Usage:
1. Open this link in browser (replace client_id & redirect_uri):
   https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=YOUR_REDIRECT_URI

2. Login → enter OTP → click Authorize.
3. You will be redirected to a URL like:
   https://www.google.com/?code=ABCDEF012345

4. Copy the code value: ABCDEF012345
5. Run this script on Render:
       python convert_token.py
6. Paste the code when asked.
7. It prints ACCESS_TOKEN.

Put that ACCESS_TOKEN in Render → Environment Variables → ACCESS_TOKEN
"""

import requests

# ------------------------------
# 🔐 ENTER YOUR APP DETAILS HERE
# ------------------------------
CLIENT_ID     = "PUT_YOUR_CLIENT_ID"
CLIENT_SECRET = "PUT_YOUR_CLIENT_SECRET"
REDIRECT_URI  = "https://www.google.com"   # Must match Upstox app settings


def convert_code_to_token(auth_code: str):
    url = "https://api.upstox.com/v2/login/authorization/token"

    payload = {
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        data = r.json()

        if "access_token" in data:
            print("\n==========================")
            print("🔥 ACCESS TOKEN GENERATED")
            print("==========================\n")
            print(data["access_token"])
        else:
            print("\n❌ ERROR RESPONSE:")
            print(data)

    except Exception as e:
        print("Exception:", e)


if __name__ == "__main__":
    print("Paste AUTH CODE (Everything after code= ):")
    code = input("> ").strip()
    if code:
        convert_code_to_token(code)
    else:
        print("No code entered.")
