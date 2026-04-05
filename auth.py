"""
Run this script once in a real terminal to complete Spotify OAuth.
It will open your browser, let you log in, then ask you to paste the
redirect URL. After that a .cache file is written and the MCP server
will authenticate silently from then on.

Usage:
    python auth.py
"""
import os
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

auth_manager = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    scope="playlist-modify-public playlist-modify-private user-read-private",
)

print("Opening Spotify login in your browser...")
print("After authorizing, copy the full redirect URL and paste it below.\n")
token = auth_manager.get_access_token(as_dict=False)

if token:
    print("\nAuth successful! .cache file written.")
    print("You can now use the MCP server — restart Claude Code if it's already running.")
else:
    print("\nAuth failed. Check your SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, and SPOTIFY_REDIRECT_URI in .env")
