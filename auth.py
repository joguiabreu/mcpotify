"""
Sign in to Spotify. Run it once during setup, and again whenever the AI says
the Spotify login needs renewing (Spotify logins expire after 6 months).

It opens your browser, catches Spotify's redirect on a local server, and saves
the token to .cache, where the MCP server picks it up without a restart.

Usage:
    python auth.py          # sign in, unless a valid login that isn't about to expire is saved
    python auth.py --force  # sign in again anyway
"""
import argparse
import secrets
import sys
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import requests
from spotipy.oauth2 import SpotifyOauthError

import spotify_auth

TIMEOUT_SECONDS = 120


class CallbackServer(HTTPServer):
    """Catches Spotify's redirect. It listens as soon as it's created, so create it before opening
    the browser: Spotify redirects at once when the user approved the app before."""

    def __init__(self, state: str):
        self.state = state
        self.result: dict[str, str] = {}
        super().__init__(("127.0.0.1", spotify_auth.REDIRECT_PORT), _CallbackHandler)

    def wait(self) -> str | None:
        """Serve the redirect URI until Spotify calls back. Returns the auth code, or None."""
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with self:
            while not self.result and time.monotonic() < deadline:
                self.timeout = max(0.1, deadline - time.monotonic())
                self.handle_request()
        if "error" in self.result:
            print(f"Spotify returned an error: {self.result['error']}")
        return self.result.get("code")


class _CallbackHandler(BaseHTTPRequestHandler):
    server: CallbackServer

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        if params.get("state", [None])[0] != self.server.state:
            # A stale tab or another page; keep waiting for this login's redirect.
            self._reply(400, b"<h1>This sign-in link has expired. Use the newest browser tab.</h1>")
            return
        if "code" in params:
            self.server.result["code"] = params["code"][0]
            self._reply(200, b"<h1>Signed in! You can close this tab.</h1>")
        else:
            self.server.result["error"] = params.get("error", ["no code received"])[0]
            self._reply(400, b"<h1>Sign-in failed.</h1>")

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # keep the terminal quiet


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign in to Spotify for the MCP server.")
    parser.add_argument("--force", action="store_true", help="sign in again even if a valid login is saved")
    args = parser.parse_args()

    try:
        auth = spotify_auth.make_auth_manager(interactive=True)
    except SpotifyOauthError as e:
        print(f"Spotify credentials are missing ({e}). Run install.py first.")
        return 1

    try:
        return _sign_in(auth, force=args.force)
    except SpotifyOauthError as e:
        if e.error == "invalid_client":
            print("Spotify rejected the app's Client ID or Secret. Re-run install.py and choose to reconfigure.")
        else:
            print(f"Spotify didn't complete the sign-in ({e.error_description or e.error or e}). Try again.")
        return 1
    except requests.exceptions.RequestException as e:
        print(f"Couldn't reach Spotify ({type(e).__name__}). Check the connection and try again.")
        return 1


def _sign_in(auth, *, force: bool) -> int:
    renewal_due = spotify_auth.login_renewal_due()
    if not force and not renewal_due and spotify_auth.has_valid_login(auth):
        print("Already signed in to Spotify.")
        expires_at = spotify_auth.login_expires_at()
        if expires_at:
            print(f"The login stays valid until about {datetime.fromtimestamp(expires_at):%d %b %Y}.")
        print("Use --force to sign in again anyway.")
        return 0
    if renewal_due:
        print("The Spotify login is about to expire, so signing in again renews it.")

    state = secrets.token_urlsafe(16)
    auth_url = auth.get_authorize_url(state=state)
    try:
        callback = CallbackServer(state)
    except OSError as e:
        print(
            f"Couldn't listen on {spotify_auth.REDIRECT_URI} for Spotify's redirect ({e.strerror or e}). "
            f"Close whatever is using port {spotify_auth.REDIRECT_PORT} (perhaps another auth.py) and try again."
        )
        return 1
    print("Opening Spotify login in your browser...")
    print(f"If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)
    print(f"Waiting for Spotify to redirect to {spotify_auth.REDIRECT_URI} ({TIMEOUT_SECONDS}s timeout)...")

    code = callback.wait()
    if not code:
        print("Sign-in failed or timed out. Check the redirect URI in your Spotify app settings and try again.")
        return 1

    auth.get_access_token(code, as_dict=False, check_cache=False)
    spotify_auth.record_login(auth)
    expires_at = spotify_auth.login_expires_at()
    print("Signed in! Token saved to .cache.")
    print(f"Spotify will ask you to sign in again around {datetime.fromtimestamp(expires_at):%d %b %Y}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
