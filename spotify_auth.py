"""
Spotify login settings shared by server.py and auth.py.

Keep every scope here: the server checks that the saved login covers all of
them, and auth.py requests exactly this list.
"""
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / ".cache"
ENV_PATH = ROOT / ".env"

REDIRECT_PORT = 8888
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"

SCOPES = [
    "playlist-modify-public",       # create/edit public playlists, remove playlists from the library
    "playlist-modify-private",      # create/edit private playlists
    "playlist-read-private",        # list the user's playlists and their items
    "user-read-private",            # read the user profile
    "user-read-currently-playing",  # currently playing item
    "user-read-playback-state",     # playback state, devices, queue
    "user-modify-playback-state",   # queue, play/pause/skip, shuffle, repeat, volume, seek, transfer
    "user-read-recently-played",    # recently played tracks
    "user-top-read",                # top tracks/artists
    "user-library-read",            # Liked Songs
    "user-library-modify",          # like/unlike tracks, save/remove albums, shows and episodes
    "user-read-playback-position",  # podcast resume points
]

# Spotify refresh tokens expire 6 months after the user authorizes the app, and
# refreshing doesn't extend that. The API doesn't expose the date, so auth.py
# records when the login happened.
LOGIN_LIFETIME_DAYS = 182
LOGIN_WARNING_DAYS = 14


class LoginRequired(Exception):
    """The saved login is missing, expired, or lacks a required scope."""


class _LoginCache(CacheFileHandler):
    """Token cache that keeps `authorized_at` when spotipy saves a refreshed token."""

    def save_token_to_cache(self, token_info):
        if "authorized_at" not in token_info and os.path.exists(self.cache_path):
            previous = self.get_cached_token() or {}
            if "authorized_at" in previous:
                token_info["authorized_at"] = previous["authorized_at"]
        super().save_token_to_cache(token_info)


class _NonInteractiveOAuth(SpotifyOAuth):
    """Raises instead of starting the browser/stdin login, which would hang a stdio MCP server."""

    def get_auth_response(self, open_browser=None):
        raise LoginRequired("No valid Spotify login is saved.")


def auth_command() -> str:
    """The command that signs in again, for messages shown to the user."""
    args = [sys.executable, str(ROOT / "auth.py")]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def make_auth_manager(*, interactive: bool = False) -> SpotifyOAuth:
    """Build the OAuth manager. Raises SpotifyOauthError if the credentials aren't configured."""
    # Read .env on every call rather than loading it into os.environ, so credentials fixed with
    # install.py reach a running server. Variables set in the real environment still win.
    settings = {**dotenv_values(ENV_PATH), **os.environ}
    cls = SpotifyOAuth if interactive else _NonInteractiveOAuth
    return cls(
        client_id=settings.get("SPOTIFY_CLIENT_ID"),
        client_secret=settings.get("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=REDIRECT_URI,
        scope=" ".join(SCOPES),
        cache_handler=_LoginCache(cache_path=str(CACHE_PATH)),
        open_browser=False,
    )


def has_valid_login(auth: SpotifyOAuth) -> bool:
    """True if the saved login covers all scopes and Spotify still accepts it.

    Always refreshes the token (a call to Spotify): an access token that hasn't expired yet doesn't
    show whether the user revoked the app's access. Raises SpotifyOauthError if Spotify rejects
    the Client ID or Secret, and requests' exceptions if it can't be reached.
    """
    token_info = auth.cache_handler.get_cached_token()
    if not token_info or _missing_scopes(token_info) or not token_info.get("refresh_token"):
        return False
    try:
        auth.refresh_access_token(token_info["refresh_token"])
    except SpotifyOauthError as e:
        if e.error == "invalid_client":
            raise  # the credentials are wrong, not the login
        return False
    return True


def record_login(auth: SpotifyOAuth) -> None:
    """Store the authorization time next to a freshly obtained token."""
    token_info = auth.cache_handler.get_cached_token()
    token_info["authorized_at"] = int(time.time())
    auth.cache_handler.save_token_to_cache(token_info)


def _cached_token() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    return _LoginCache(cache_path=str(CACHE_PATH)).get_cached_token()


def _missing_scopes(token_info: dict) -> list[str]:
    return sorted(set(SCOPES) - set((token_info.get("scope") or "").split()))


def cached_login_problem() -> str | None:
    """Describe what's wrong with the saved login without calling Spotify, or None if it looks usable."""
    token_info = _cached_token()
    if not token_info:
        return "no Spotify login is saved"
    missing = _missing_scopes(token_info)
    if missing:
        return f"the saved login is missing permissions: {', '.join(missing)}"
    return None


def login_expires_at() -> float | None:
    """Unix time when the saved login stops working, if auth.py recorded when it happened."""
    authorized_at = (_cached_token() or {}).get("authorized_at")
    if authorized_at is None:
        return None
    return authorized_at + LOGIN_LIFETIME_DAYS * 86400


def login_renewal_due() -> bool:
    """True when the saved login expires within LOGIN_WARNING_DAYS, so auth.py should renew it."""
    expires_at = login_expires_at()
    return expires_at is not None and expires_at - time.time() <= LOGIN_WARNING_DAYS * 86400
