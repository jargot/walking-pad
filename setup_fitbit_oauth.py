#!/usr/bin/env python3

import os
import sys
import base64
import requests
from urllib.parse import urlencode, urlparse, parse_qs
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('FITBIT_CLIENT_ID')
CLIENT_SECRET = os.getenv('FITBIT_CLIENT_SECRET')
REDIRECT_URI = os.getenv('FITBIT_REDIRECT_URI')

def generate_auth_url():
    """Generate the authorization URL for Fitbit OAuth."""
    scopes = ['activity', 'profile']
    params = {
        'response_type': 'token',  # Use implicit grant for year-long tokens
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI,
        'scope': ' '.join(scopes),
        'expires_in': '31536000'  # 1 year
    }
    return f"https://www.fitbit.com/oauth2/authorize?{urlencode(params)}"

def extract_token_from_url(url):
    """Extract access token from the redirect URL fragment (implicit grant)."""
    parsed = urlparse(url)
    # Tokens are in the fragment (after #) for implicit grant
    fragment = parsed.fragment
    if not fragment:
        return None

    params = parse_qs(fragment)
    return {
        'access_token': params.get('access_token', [None])[0],
        'expires_in': params.get('expires_in', [None])[0],
        'token_type': params.get('token_type', [None])[0],
        'user_id': params.get('user_id', [None])[0]
    }

def main():
    if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
        print("❌ Missing required environment variables. Check your .env file.")
        sys.exit(1)

    args = sys.argv[1:]

    if len(args) == 0:
        print("🔗 Fitbit OAuth Setup (1 Year Token)\n")
        print("Step 1: Visit this URL to authorize the app:")
        print(generate_auth_url())
        print("\nStep 2: After authorization, you'll be redirected to your redirect URL.")
        print("The URL will contain a token after the # symbol (fragment).")
        print("Step 3: Copy the FULL redirect URL (including everything after #) and run:")
        print("python setup_fitbit_oauth.py <full_redirect_url_with_fragment>")
        print("\nExample URL format: https://www.jeromeargot.com/asd#access_token=...")
        return

    # Extract token from URL
    redirect_url = args[0]
    tokens = extract_token_from_url(redirect_url)

    if not tokens or not tokens['access_token']:
        print("❌ Could not extract access token from URL")
        print("Make sure you copied the full redirect URL including everything after the # symbol")
        sys.exit(1)

    print("✅ Success! Token extracted from URL:")
    print(f"FITBIT_ACCESS_TOKEN={tokens['access_token']}")
    print(f"\nToken expires in: {tokens['expires_in']} seconds ({int(tokens['expires_in'])//86400} days)")
    print(f"User ID: {tokens['user_id']}")
    print(f"Token type: {tokens['token_type']}")

    print(f"\n📝 Add this to your .env file:")
    print(f"FITBIT_ACCESS_TOKEN={tokens['access_token']}")
    print(f"\n⚠️  Note: Implicit grant tokens cannot be refreshed.")
    print("You'll need to re-authorize when the token expires.")

if __name__ == "__main__":
    main()