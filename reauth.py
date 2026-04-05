"""
Run this whenever the MCP server hangs on login:

    .venv/bin/python reauth.py

It will open a browser, ask you to log in to Spotify, and save a fresh
token to .cache. After that, restart Claude Code and you're good to go.
"""
import os
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth
import spotipy

load_dotenv()

cache_path = os.path.join(os.path.dirname(__file__), ".cache")
if os.path.exists(cache_path):
    os.remove(cache_path)
    print("Deleted stale .cache")

auth = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    scope="playlist-modify-public playlist-modify-private user-read-private",
)

sp = spotipy.Spotify(auth_manager=auth)
user = sp.current_user()
print(f"Authenticated as: {user['display_name']} ({user['id']})")
print("Token saved to .cache — restart Claude Code and you're good to go.")
