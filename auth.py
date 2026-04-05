"""
Run this script once to complete Spotify OAuth.
It will open your browser, handle the callback automatically via a local server,
and write a .cache file. The MCP server will authenticate silently from then on.

Usage:
    python auth.py
"""
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

PORT = 8888
REDIRECT_URI = f"http://127.0.0.1:{PORT}/callback"

SCOPES = " ".join([
    "playlist-modify-public",
    "playlist-modify-private",
    "playlist-read-private",
    "user-read-private",
    "user-read-currently-playing",
    "user-modify-playback-state",
    "user-top-read",
])

auth_manager = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=REDIRECT_URI,
    scope=SCOPES,
    open_browser=False,
)

auth_url = auth_manager.get_authorize_url()
print("Opening Spotify login in your browser...")
webbrowser.open(auth_url)
print(f"Waiting for callback on http://127.0.0.1:{PORT}/callback (60s timeout)...\n")

code = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global code
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            code = params["code"][0]
            body = b"<h1>Auth successful! You can close this tab.</h1>"
            self.send_response(200)
        else:
            body = b"<h1>Auth failed. No code received.</h1>"
            self.send_response(400)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress server logs


server = HTTPServer(("127.0.0.1", PORT), _CallbackHandler)
server.timeout = 60
server.handle_request()

if code:
    auth_manager.get_access_token(code)
    print("Auth successful! .cache file written.")
    print("You can now use the MCP server — restart Claude Code if it's already running.")
else:
    print("Auth failed or timed out. Check your Spotify app settings and try again.")
