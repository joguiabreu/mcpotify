"""
Spotify MCP server: search, playlists, library, queue and playback.

Every Spotify call goes through _spotify(), which runs spotipy off the event
loop, retries rate limits, and turns failures into messages the model can act on.
"""
import asyncio
import json
import logging
import logging.handlers
import os
import random
import re
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

import requests
import requests.adapters
import spotipy
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from spotipy.oauth2 import SpotifyOauthError
from urllib3.util import Retry

import spotify_auth

log = logging.getLogger("mcp-spotify")
LOG_PATH = spotify_auth.ROOT / "mcp-spotify.log"

# Page and batch limits from the Spotify OpenAPI spec
SEARCH_PAGE_MAX = 10         # GET /search
ARTIST_ALBUMS_PAGE_MAX = 10  # GET /artists/{id}/albums
PAGE_MAX = 50                # other paginated GETs
PLAYLIST_WRITE_MAX = 100     # POST/DELETE /playlists/{id}/items
LIBRARY_WRITE_MAX = 40       # PUT/DELETE /me/library
PLAYLIST_MAX_ITEMS = 10_000

MAX_ATTEMPTS = 4
MAX_RETRY_WAIT = 30  # seconds; a longer Retry-After fails fast instead of stalling the tool call
MAX_CONCURRENT_SEARCHES = 5


# --- Logging ---

class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating log whose files are readable only by the current user."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return open(fd, self.mode, encoding=self.encoding, errors=self.errors)


def _configure_logging() -> None:
    handler = _PrivateRotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    # MCPServer() already gave the root logger a stderr handler, so basicConfig() would do nothing.
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    # spotipy's DEBUG output includes access tokens, the client secret and full API responses.
    for noisy in ("spotipy", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    level = os.getenv("MCPOTIFY_LOG_LEVEL", "INFO").upper()
    try:
        log.setLevel(level)
    except ValueError:
        log.setLevel(logging.INFO)
        log.warning("Unknown MCPOTIFY_LOG_LEVEL %r; using INFO", level)
    # Older versions logged tokens and the client secret to world-readable files, rotated ones included.
    for path in LOG_PATH.parent.glob(f"{LOG_PATH.name}*"):
        os.chmod(path, 0o600)


# --- Errors ---

class SpotifyError(ToolError):
    """A Spotify failure explained for the model. `fatal` ones stop multi-item operations."""

    def __init__(self, message: str, *, fatal: bool = True):
        super().__init__(message)
        self.fatal = fatal


_PLAYER_METHODS = {
    "add_to_queue", "start_playback", "pause_playback", "next_track", "previous_track",
    "shuffle", "repeat", "volume", "seek_track", "transfer_playback",
}

_PLAYER_REASONS = {
    "ALREADY_PAUSED": "Playback is already paused.",
    "NOT_PAUSED": "Spotify is already playing.",
    "ALREADY_PLAYING": "Spotify is already playing.",
    "NO_NEXT_TRACK": "There is no next track.",
    "NO_PREV_TRACK": "There is no previous track.",
    "VOLUME_CONTROL_DISALLOW": "This device doesn't allow volume control.",
    "DEVICE_NOT_CONTROLLABLE": "This device can't be controlled remotely.",
    "REMOTE_CONTROL_DISALLOW": "This device can't be controlled remotely.",
}


def _login_error(detail: str = "") -> SpotifyError:
    reason = f" ({detail})" if detail else ""
    return SpotifyError(
        f"The Spotify login needs renewing{reason}. "
        f"Ask the user to run this in a terminal, then try again:\n  {spotify_auth.auth_command()}"
    )


def _explain(e: spotipy.SpotifyException, method: str) -> SpotifyError:
    status, reason = e.http_status, e.reason
    detail = str(e.msg).split(":\n", 1)[-1].strip()
    player = method in _PLAYER_METHODS
    if status == 401:
        return _login_error(detail)
    if reason == "PREMIUM_REQUIRED" or (status == 403 and player and "premium" in detail.lower()):
        return SpotifyError(
            f"Spotify refused the playback command ({detail}). "
            "Playback control and the queue require Spotify Premium."
        )
    # A player 404 can also mean the track or context doesn't exist, so it has to mention the device.
    if reason == "NO_ACTIVE_DEVICE" or (status == 404 and player and "device" in detail.lower()):
        return SpotifyError(
            f"No active Spotify device ({detail}). Open Spotify on a device, or call list_devices and "
            "pass a device_id, then try again."
        )
    if reason in _PLAYER_REASONS:
        return SpotifyError(_PLAYER_REASONS[reason])
    if status == 403 and player:
        return SpotifyError(
            f"Spotify refused the playback command ({detail}). The device, or what's playing, "
            "may not allow it right now."
        )
    if status == 429:
        wait = (e.headers or {}).get("Retry-After")
        hint = f" and asks to wait {wait}s" if wait else ""
        return SpotifyError(f"Spotify is rate limiting requests{hint}. Try again later.")
    if status >= 500:
        return SpotifyError(f"Spotify is having problems (HTTP {status}). Try again shortly.")
    if status == 403 and method == "playlist_items":
        return SpotifyError("Spotify only shares the items of playlists the user owns or collaborates on.", fatal=False)
    if status == 403:
        return SpotifyError(f"Spotify refused this request: {detail}")
    if status == 404:
        return SpotifyError(f"Not found on Spotify: {detail}", fatal=False)
    return SpotifyError(f"Spotify rejected the request (HTTP {status}): {detail}", fatal=False)


# --- Spotify client ---

sp: spotipy.Spotify | None = None  # created on first use; tests replace it with a mock


def _session() -> requests.Session:
    """HTTP session for spotipy that only retries what's safe to send again.

    spotipy's own session turns exhausted retries into a fake 429, and lets urllib3 sleep out
    any Retry-After. Here urllib3 retries failed connections (nothing reached Spotify) and 5xx
    on GETs, then hands back the last response so the real status reaches _explain(). Writes
    aren't resent after a 5xx, since Spotify may have applied them. 429s are left to _spotify().
    """
    retry = Retry(
        total=3,
        read=False,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        backoff_factor=0.3,
        respect_retry_after_header=False,
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", requests.adapters.HTTPAdapter(max_retries=retry))
    return session


def _client() -> spotipy.Spotify:
    global sp
    if sp is None:
        try:
            auth = spotify_auth.make_auth_manager()
        except SpotifyOauthError as e:
            raise SpotifyError(
                f"Spotify credentials aren't configured ({e}). Run install.py in {spotify_auth.ROOT}."
            ) from e
        sp = spotipy.Spotify(auth_manager=auth, requests_session=_session(), requests_timeout=15)
    return sp


def _retry_after(e: spotipy.SpotifyException, attempt: int) -> float:
    try:
        return float((e.headers or {}).get("Retry-After"))
    except (TypeError, ValueError):
        return float(2 ** (attempt - 1))


async def _spotify(method: str, *args: Any, **kwargs: Any) -> Any:
    """Call a spotipy client method by name, off the event loop.

    Retries 429s (honouring Retry-After up to MAX_RETRY_WAIT) and raises
    SpotifyError with an explanation the model can act on.
    """
    fn = getattr(_client(), method)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        t0 = time.perf_counter()
        try:
            result = await asyncio.to_thread(fn, *args, **kwargs)
        except spotipy.SpotifyException as e:
            if e.http_status == 429 and attempt < MAX_ATTEMPTS:
                wait = _retry_after(e, attempt)
                if wait <= MAX_RETRY_WAIT:
                    wait += random.uniform(0, 1)
                    log.warning("Rate limited on %s, retrying in %.1fs (attempt %d/%d)", method, wait, attempt, MAX_ATTEMPTS)
                    await asyncio.sleep(wait)
                    continue
            log.warning("Spotify %s failed: HTTP %s %s", method, e.http_status, e.msg)
            raise _explain(e, method) from e
        except spotify_auth.LoginRequired as e:
            problem = spotify_auth.cached_login_problem() or ""
            log.warning("Spotify login required for %s: %s", method, problem)
            raise _login_error(problem) from e
        except SpotifyOauthError as e:
            log.warning("Spotify login failed for %s: %s %s", method, e.error, e.error_description)
            if e.error == "invalid_client":
                global sp
                sp = None  # the next call rereads .env, so fixed credentials work without a restart
                raise SpotifyError(
                    "Spotify rejected the app's Client ID or Secret. Ask the user to re-run install.py, "
                    "choose to reconfigure, then try again."
                ) from e
            raise _login_error(e.error_description or e.error or "") from e
        except requests.exceptions.RequestException as e:
            log.warning("Spotify %s failed: %r", method, e)
            raise SpotifyError(f"Couldn't reach Spotify ({type(e).__name__}). Check the connection and try again.") from e
        log.debug("Spotify %s took %.2fs", method, time.perf_counter() - t0)
        return result


async def _collect(
    fetch: Callable[[int, int], Awaitable[dict]], limit: int, page_max: int, offset: int = 0
) -> tuple[list[Any], int | None, int | None]:
    """Fetch up to `limit` items, `page_max` at a time. `fetch(size, offset)` returns a paging object.

    Returns (items, total, next_offset). Items keep their API order and may include nulls.
    """
    items: list[Any] = []
    page: dict = {}
    while len(items) < limit:
        size = min(page_max, limit - len(items))
        page = await fetch(size, offset + len(items))
        page_items = page.get("items") or []
        items.extend(page_items)
        if len(page_items) < size or not page.get("next"):
            break
    next_offset = offset + len(items) if page.get("next") else None
    return items, page.get("total"), next_offset


async def _search(query: str, kind: str, limit: int) -> list[dict]:
    """Search one item type for up to `limit` unique results.

    Spotify caps search pages at 10, and for filtered queries (artist:...) it returns
    short pages with no `next` link even though later offsets have more results. So
    this walks offsets 10 at a time until it has enough or a page comes back empty.
    """
    found: dict[str, dict] = {}
    max_pages = 2 * -(-limit // SEARCH_PAGE_MAX)
    for page in range(max_pages):
        result = await _spotify("search", q=query, type=kind, limit=SEARCH_PAGE_MAX, offset=page * SEARCH_PAGE_MAX)
        items = [item for item in result[f"{kind}s"]["items"] if item]
        if not items:
            break
        for item in items:
            found.setdefault(item["uri"], item)
        if len(found) >= limit:
            break
    return list(found.values())[:limit]


async def _playlist_entries(playlist_id: str, fields: str, **kwargs: Any) -> list[dict]:
    """Every entry of a playlist, in order (including nulls, so indexes are positions)."""
    async def fetch(size: int, offset: int) -> dict:
        return await _spotify("playlist_items", playlist_id, fields=fields, limit=size, offset=offset, **kwargs)

    entries, _, _ = await _collect(fetch, PLAYLIST_MAX_ITEMS, PAGE_MAX)
    return entries


async def _playlist_slots(playlist_id: str) -> list[str | None]:
    """The URI at each position of a playlist; None where the item was removed or is a local file."""
    entries = await _playlist_entries(playlist_id, "items(is_local,item(uri)),next,total")
    slots: list[str | None] = [None] * len(entries)
    for index, item in _playlist_items(entries)[0]:
        slots[index] = item["uri"]
    return slots


# --- Input helpers ---

_SPOTIFY_REF = re.compile(
    r"^(?:spotify:(?P<uri_type>[a-z]+):|https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(?P<url_type>[a-z]+)/)?"
    r"(?P<id>[A-Za-z0-9]+)(?:[?#].*)?$"
)


def _parse_ref(value: str) -> tuple[str | None, str]:
    match = _SPOTIFY_REF.match(value.strip())
    if not match:
        raise ToolError(f"Not a Spotify URI, URL or ID: {value!r}")
    return match["uri_type"] or match["url_type"], match["id"]


def _id(value: str, kind: str) -> str:
    """Accept a Spotify URI, open.spotify.com URL or bare ID, and return the ID."""
    ref_kind, ref_id = _parse_ref(value)
    if ref_kind not in (None, kind):
        raise ToolError(f"Expected a Spotify {kind}, got a {ref_kind}: {value!r}")
    return ref_id


def _uri(value: str, allowed: tuple[str, ...] = ("track",)) -> str:
    """Normalize a URI, URL or bare ID (assumed to be `allowed[0]`) to a spotify: URI."""
    ref_kind, ref_id = _parse_ref(value)
    kind = ref_kind or allowed[0]
    if kind not in allowed:
        raise ToolError(f"Expected a Spotify {' or '.join(allowed)}, got a {kind}: {value!r}")
    return f"spotify:{kind}:{ref_id}"


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


# --- Output models ---

class Track(BaseModel):
    name: str
    artists: list[str] = Field(description="Artist names; for podcast episodes, the show")
    uri: str
    album: str | None = None


class PlaylistTrack(Track):
    position: int = Field(description="0-based position in the playlist, as used by reorder_playlist")


class PlayedTrack(Track):
    played_at: str


class Artist(BaseModel):
    name: str
    uri: str


class Album(BaseModel):
    name: str
    artists: list[str]
    year: str | None = None
    type: str | None = Field(None, description="album, single or compilation")
    total_tracks: int | None = None
    uri: str


class Playlist(BaseModel):
    name: str
    id: str
    owner: str | None = None
    tracks: int | None = Field(None, description="Number of items; Spotify may omit it for playlists the user doesn't own")
    public: bool | None = None


class Show(BaseModel):
    name: str
    uri: str
    description: str | None = None
    total_episodes: int | None = None


class Episode(BaseModel):
    name: str
    uri: str
    show: str | None = None
    release_date: str | None = None
    duration_minutes: int | None = None
    resume_minutes: int | None = Field(None, description="Where the user stopped listening")
    fully_played: bool | None = None


class Device(BaseModel):
    id: str | None = Field(None, description="Pass as device_id; restricted devices have none")
    name: str
    type: str
    is_active: bool
    volume_percent: int | None = None


class Page(BaseModel):
    total: int | None = None
    next_offset: int | None = Field(None, description="Pass as offset to get the next page; absent when there are no more")


class TrackList(BaseModel):
    tracks: list[Track]


class TrackPage(Page):
    tracks: list[Track]


class PlaylistTracks(Page):
    tracks: list[PlaylistTrack]
    skipped_local_files: int = 0


class TopTracks(BaseModel):
    period: str
    tracks: list[Track]


class TopArtists(BaseModel):
    period: str
    artists: list[Artist]


class ArtistList(BaseModel):
    artists: list[Artist]


class AlbumList(BaseModel):
    albums: list[Album]


class PlaylistPage(Page):
    playlists: list[Playlist]


class PodcastResults(BaseModel):
    shows: list[Show] | None = None
    episodes: list[Episode] | None = None


class EpisodePage(Page):
    episodes: list[Episode]


class RecentlyPlayed(BaseModel):
    tracks: list[PlayedTrack]


class PlaybackState(BaseModel):
    is_playing: bool
    item: Track | None = None
    artist_uris: list[str] = Field([], description="URIs of the item's artists, e.g. for get_artist_albums")
    device: Device | None = None
    context_uri: str | None = Field(None, description="The album, playlist or show being played")
    progress_seconds: int | None = None
    shuffle: bool | None = None
    repeat: str | None = None


class QueueState(BaseModel):
    currently_playing: Track | None = None
    queue: list[Track]


class DeviceList(BaseModel):
    devices: list[Device]


class DuplicateTrack(BaseModel):
    name: str
    artists: list[str]
    uri: str
    positions: list[int]


class LikelyDuplicate(BaseModel):
    name: str = Field(description="Shared base title, ignoring version suffixes like '- Remastered'")
    versions: list[PlaylistTrack]


class PlaylistIssues(BaseModel):
    playlist_id: str
    name: str
    duplicates: list[DuplicateTrack] = Field([], description="The same track more than once")
    likely_duplicates: list[LikelyDuplicate] = Field([], description="Different versions of the same song by the same artist")
    unavailable: list[PlaylistTrack] = Field([], description="Tracks that can't be played in the user's country")
    local_files: int = 0


class PlaylistAudit(BaseModel):
    playlists_checked: int
    playlists_with_issues: list[PlaylistIssues]
    skipped: list[str] = Field([], description="Playlists that couldn't be checked, and why")
    stopped_early: str | None = Field(
        None, description="Why the check stopped before the last playlist; the results cover only the playlists checked",
    )


def _result(model: BaseModel) -> CallToolResult:
    """Structured output, plus the same data as compact JSON text for the model."""
    data = model.model_dump(mode="json", exclude_defaults=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def _names(objects: list[dict] | None) -> list[str]:
    return [o["name"] for o in objects or [] if o]


def _track(item: dict) -> Track:
    if item.get("type") == "episode":
        show = item.get("show") or {}
        return Track(name=item["name"], artists=[show["name"]] if show.get("name") else [], uri=item["uri"])
    album = item.get("album") or {}
    return Track(name=item["name"], artists=_names(item.get("artists")), uri=item["uri"], album=album.get("name"))


def _entry_item(entry: dict | None) -> dict | None:
    """The track or episode in a playlist/library entry (`item`, formerly `track`)."""
    if not entry:
        return None
    return entry.get("item") or entry.get("track")


def _playlist_items(entries: list[dict | None]) -> tuple[list[tuple[int, dict]], int]:
    """The Spotify tracks and episodes among playlist entries as (index, item), and the number of local files.

    Removed items (null) and local files are left out but still count towards the indexes,
    so an index plus the page's offset is the item's position in the playlist.
    """
    items, local = [], 0
    for index, entry in enumerate(entries):
        item = _entry_item(entry)
        if not item or not item.get("uri"):
            continue
        if entry.get("is_local") or item["uri"].startswith("spotify:local:"):
            local += 1
            continue
        items.append((index, item))
    return items, local


def _album(album: dict) -> Album:
    return Album(
        name=album["name"],
        artists=_names(album.get("artists")),
        year=(album.get("release_date") or "")[:4] or None,
        type=album.get("album_type"),
        total_tracks=album.get("total_tracks"),
        uri=album["uri"],
    )


def _playlist(playlist: dict) -> Playlist:
    count = playlist.get("items") or playlist.get("tracks")  # renamed from `tracks` in 2026
    return Playlist(
        name=playlist["name"],
        id=playlist["id"],
        owner=(playlist.get("owner") or {}).get("display_name"),
        tracks=count.get("total") if isinstance(count, dict) else None,
        public=playlist.get("public"),
    )


def _minutes(ms: int | None) -> int | None:
    return round(ms / 60000) if ms else None


def _show(show: dict) -> Show:
    description = " ".join((show.get("description") or "").split())
    return Show(
        name=show["name"],
        uri=show["uri"],
        description=description[:200] + ("…" if len(description) > 200 else "") or None,
        total_episodes=show.get("total_episodes"),
    )


def _episode(episode: dict) -> Episode:
    resume = episode.get("resume_point") or {}
    return Episode(
        name=episode["name"],
        uri=episode["uri"],
        show=(episode.get("show") or {}).get("name"),
        release_date=episode.get("release_date"),
        duration_minutes=_minutes(episode.get("duration_ms")),
        resume_minutes=_minutes(resume.get("resume_position_ms")),
        fully_played=resume.get("fully_played") or None,
    )


def _device(device: dict) -> Device:
    return Device(
        id=device.get("id"),
        name=device["name"],
        type=device.get("type", "Unknown"),
        is_active=device.get("is_active", False),
        volume_percent=device.get("volume_percent"),
    )


_PERIODS = {"short_term": "last 4 weeks", "medium_term": "last 6 months", "long_term": "all time"}

# --- Server ---

INSTRUCTIONS = """\
Tools for the user's Spotify account. The data comes from Spotify; attribute it to Spotify when presenting results.

Finding music
- Spotify search matches text literally against names. Mood words ("dreamy", "melancholic") match song titles, not \
sound. Translate a mood into artists you know fit it, then search one artist at a time ('artist:Folamour'), or pass \
the artists to queue_recommendations. Spotify returns nothing when field filters are combined with OR \
('artist:A OR artist:B') or when genre: is combined with artist:. Spotify has no recommendations API: your music \
knowledge is the recommender. get_current_playing, get_recently_played and get_my_top_artists give taste context.
- Never guess URIs or IDs; get them from a search or list tool.
- If a search returns nothing, stop and suggest a broader query (fewer words, artist name only, check spelling).
- When queuing or adding, use the top result if it clearly matches. If it's a cover, a different artist, or the \
request is ambiguous, show the top 3-5 and ask.

Confirm vs. proceed
- Just do it: playback controls, browsing, queuing up to about 10 tracks for a clear request.
- Ask first: queuing more than about 10 tracks, adding tracks to a playlist (list them), creating a playlist (show \
the tracklist), and anything that removes or deletes.
- Show about 10 items of a long list and offer more rather than dumping everything.

When something fails
- If a step fails mid-workflow, stop. Tell the user what completed, what failed and why, and ask how to continue.
- Don't retry Premium, device or sign-in errors; relay their instructions to the user.
- Playback and queue tools need Spotify Premium and an active device. If none is active, use list_devices and pass \
a device_id, or transfer_playback.
"""


async def _login_expiry_notice(ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
    """Add a renewal reminder to tool results while the Spotify login is about to expire.

    Checked on every call rather than added to INSTRUCTIONS, which clients read once per
    connection: a long-running server would never warn, or keep warning after a renewal.
    """
    result = await call_next(ctx)
    # Middleware sees the serialized result; elicitation requests have no content to add to.
    if ctx.method != "tools/call" or not isinstance(result, dict) or "content" not in result or result.get("isError"):
        return result
    if not spotify_auth.login_renewal_due():
        return result
    notice = (
        f"Note: the user's Spotify login expires around {datetime.fromtimestamp(spotify_auth.login_expires_at()):%d %b %Y}. "
        f"Mention once that they can renew it by running: {spotify_auth.auth_command()}"
    )
    return {**result, "content": [*result["content"], {"type": "text", "text": notice}]}


@asynccontextmanager
async def _lifespan(server: MCPServer):
    log.info("MCP server starting (pid=%d)", os.getpid())
    problem = spotify_auth.cached_login_problem()
    if problem:
        log.warning("Spotify login problem: %s. Run: %s", problem, spotify_auth.auth_command())
    yield None
    log.info("MCP server shut down")


mcp = MCPServer(
    "mcp-spotify", title="Spotify", instructions=INSTRUCTIONS, lifespan=_lifespan, middleware=[_login_expiry_notice],
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def _changes(*, destructive: bool = False, idempotent: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=False, destructive_hint=destructive, idempotent_hint=idempotent, open_world_hint=True
    )


Query = Annotated[str, Field(
    description="Search text. Supports filters such as artist:, album:, track:, year:1990-1999, genre: and tag:new; "
    "filters can't be combined with OR.",
)]
PlaylistRef = Annotated[str, Field(description="Spotify playlist ID, URI or URL (from list_my_playlists)")]
TrackUris = Annotated[list[str], Field(min_length=1, description="Spotify track URIs, e.g. ['spotify:track:abc123']")]
TimeRange = Annotated[
    Literal["short_term", "medium_term", "long_term"],
    Field(description="short_term: last 4 weeks, medium_term: last 6 months, long_term: all time"),
]
DeviceId = Annotated[str | None, Field(description="Device to use (from list_devices); defaults to the active one")]


Limit50 = Annotated[int, Field(ge=1, le=50, description="Maximum number of results (1-50)")]
Limit200 = Annotated[int, Field(ge=1, le=200, description="Maximum number of results (1-200)")]
Limit500 = Annotated[int, Field(ge=1, le=500, description="Maximum number of results (1-500)")]
Offset = Annotated[int, Field(ge=0, description="Index of the first item, for paging (see next_offset)")]


# --- Search and browse ---

@mcp.tool(title="Search tracks", annotations=READ_ONLY)
async def search_tracks(query: Query, limit: Limit50 = 10) -> Annotated[CallToolResult, TrackList]:
    """Search Spotify for tracks. Returns track names, artists, albums and URIs."""
    items = await _search(query, "track", limit)
    return _result(TrackList(tracks=[_track(t) for t in items]))


@mcp.tool(title="Search artists", annotations=READ_ONLY)
async def search_artists(query: Query, limit: Limit50 = 10) -> Annotated[CallToolResult, ArtistList]:
    """Search Spotify for artists. Use an artist URI with get_artist_albums to browse their discography."""
    items = await _search(query, "artist", limit)
    return _result(ArtistList(artists=[Artist(name=a["name"], uri=a["uri"]) for a in items]))


@mcp.tool(title="Search albums", annotations=READ_ONLY)
async def search_albums(query: Query, limit: Limit50 = 10) -> Annotated[CallToolResult, AlbumList]:
    """Search Spotify for albums. Use an album URI with get_album_tracks to get the tracklist."""
    items = await _search(query, "album", limit)
    return _result(AlbumList(albums=[_album(a) for a in items]))


@mcp.tool(title="Search podcasts", annotations=READ_ONLY)
async def search_podcasts(
    query: Query,
    kind: Annotated[Literal["show", "episode"], Field(description="Search podcast shows or individual episodes")] = "show",
    limit: Limit50 = 10,
) -> Annotated[CallToolResult, PodcastResults]:
    """Search Spotify for podcast shows or episodes. Use a show URI with get_show_episodes."""
    items = await _search(query, kind, limit)
    if kind == "show":
        return _result(PodcastResults(shows=[_show(s) for s in items]))
    return _result(PodcastResults(episodes=[_episode(e) for e in items]))


@mcp.tool(title="Get artist's albums", annotations=READ_ONLY)
async def get_artist_albums(
    artist_uri: Annotated[str, Field(description="Spotify artist URI, URL or ID")],
    include_groups: Annotated[
        list[Literal["album", "single", "appears_on", "compilation"]] | None,
        Field(description="Release types to include (default: album and single)"),
    ] = None,
    limit: Limit50 = 20,
) -> Annotated[CallToolResult, AlbumList]:
    """Get an artist's releases, newest first. Use search_artists to get the artist URI."""
    artist_id = _id(artist_uri, "artist")
    groups = ",".join(include_groups or ["album", "single"])

    async def fetch(size: int, offset: int) -> dict:
        return await _spotify("artist_albums", artist_id, include_groups=groups, limit=size, offset=offset)

    albums, _, _ = await _collect(fetch, limit, ARTIST_ALBUMS_PAGE_MAX)
    return _result(AlbumList(albums=[_album(a) for a in albums if a]))


@mcp.tool(title="Get album tracks", annotations=READ_ONLY)
async def get_album_tracks(
    album_uri: Annotated[str, Field(description="Spotify album URI, URL or ID")],
) -> Annotated[CallToolResult, TrackList]:
    """Get every track on an album, in order."""
    album_id = _id(album_uri, "album")

    async def fetch(size: int, offset: int) -> dict:
        return await _spotify("album_tracks", album_id, limit=size, offset=offset)

    tracks, _, _ = await _collect(fetch, 1000, PAGE_MAX)
    return _result(TrackList(tracks=[_track(t) for t in tracks if t]))


@mcp.tool(title="Get podcast episodes", annotations=READ_ONLY)
async def get_show_episodes(
    show_uri: Annotated[str, Field(description="Spotify show URI, URL or ID")],
    limit: Limit50 = 10,
    offset: Offset = 0,
) -> Annotated[CallToolResult, EpisodePage]:
    """Get a podcast show's episodes, newest first, including where the user stopped listening."""
    page = await _spotify("show_episodes", _id(show_uri, "show"), limit=limit, offset=offset)
    episodes = [_episode(e) for e in page.get("items") or [] if e]
    next_offset = offset + len(page.get("items") or []) if page.get("next") else None
    return _result(EpisodePage(episodes=episodes, total=page.get("total"), next_offset=next_offset))


# --- The user's listening ---

@mcp.tool(title="Get my top tracks", annotations=READ_ONLY)
async def get_my_top_tracks(
    limit: Limit50 = 10, time_range: TimeRange = "medium_term"
) -> Annotated[CallToolResult, TopTracks]:
    """Get the user's most listened-to tracks. Useful taste context for picking music."""
    results = await _spotify("current_user_top_tracks", limit=limit, time_range=time_range)
    tracks = [_track(t) for t in results.get("items") or [] if t]
    return _result(TopTracks(period=_PERIODS[time_range], tracks=tracks))


@mcp.tool(title="Get my top artists", annotations=READ_ONLY)
async def get_my_top_artists(
    limit: Limit50 = 10, time_range: TimeRange = "medium_term"
) -> Annotated[CallToolResult, TopArtists]:
    """Get the user's most listened-to artists. Useful taste context for picking music."""
    results = await _spotify("current_user_top_artists", limit=limit, time_range=time_range)
    artists = [Artist(name=a["name"], uri=a["uri"]) for a in results.get("items") or [] if a]
    return _result(TopArtists(period=_PERIODS[time_range], artists=artists))


@mcp.tool(title="Get recently played", annotations=READ_ONLY)
async def get_recently_played(limit: Limit50 = 20) -> Annotated[CallToolResult, RecentlyPlayed]:
    """Get the tracks the user played most recently, newest first. Good context for 'more like this'."""
    results = await _spotify("current_user_recently_played", limit=limit)
    tracks = [
        PlayedTrack(**_track(entry["track"]).model_dump(), played_at=entry["played_at"])
        for entry in results.get("items") or []
        if entry and entry.get("track")
    ]
    return _result(RecentlyPlayed(tracks=tracks))


# --- Library ---

_LIBRARY_KINDS = ("track", "album", "episode", "show", "audiobook")
LibraryUris = Annotated[list[str], Field(
    min_length=1, description="Spotify URIs of tracks, albums, podcast episodes or shows",
)]


@mcp.tool(title="Get Liked Songs", annotations=READ_ONLY)
async def get_liked_songs(
    limit: Limit200 = 50, offset: Offset = 0
) -> Annotated[CallToolResult, TrackPage]:
    """Get the user's Liked Songs, most recently liked first."""
    async def fetch(size: int, page_offset: int) -> dict:
        return await _spotify("current_user_saved_tracks", limit=size, offset=page_offset)

    entries, total, next_offset = await _collect(fetch, limit, PAGE_MAX, offset)
    tracks = [_track(item) for item in map(_entry_item, entries) if item]
    return _result(TrackPage(tracks=tracks, total=total, next_offset=next_offset))


@mcp.tool(title="Save to library", annotations=_changes(idempotent=True), structured_output=False)
async def save_to_library(uris: LibraryUris) -> str:
    """Like tracks (adds them to Liked Songs) or save albums, podcast shows or episodes to the user's library."""
    normalized = _unique(_uri(u, _LIBRARY_KINDS) for u in uris)
    for batch in _chunks(normalized, LIBRARY_WRITE_MAX):
        await _spotify("_put", "me/library", uris=",".join(batch))
    return f"Saved {len(normalized)} item(s) to the library."


@mcp.tool(title="Remove from library", annotations=_changes(destructive=True, idempotent=True), structured_output=False)
async def remove_from_library(uris: LibraryUris) -> str:
    """Unlike tracks (removes them from Liked Songs) or remove albums, podcast shows or episodes from the library."""
    normalized = _unique(_uri(u, _LIBRARY_KINDS) for u in uris)
    for batch in _chunks(normalized, LIBRARY_WRITE_MAX):
        await _spotify("_delete", "me/library", uris=",".join(batch))
    return f"Removed {len(normalized)} item(s) from the library."


# --- Playlists ---

@mcp.tool(title="List my playlists", annotations=READ_ONLY)
async def list_my_playlists(
    limit: Limit200 = 20, offset: Offset = 0
) -> Annotated[CallToolResult, PlaylistPage]:
    """List the user's playlists (owned and followed) with IDs and item counts."""
    async def fetch(size: int, page_offset: int) -> dict:
        return await _spotify("current_user_playlists", limit=size, offset=page_offset)

    playlists, total, next_offset = await _collect(fetch, limit, PAGE_MAX, offset)
    return _result(PlaylistPage(playlists=[_playlist(p) for p in playlists if p], total=total, next_offset=next_offset))


@mcp.tool(title="Get playlist tracks", annotations=READ_ONLY)
async def get_playlist_tracks(
    playlist_id: PlaylistRef, limit: Limit500 = 100, offset: Offset = 0
) -> Annotated[CallToolResult, PlaylistTracks]:
    """Get a playlist's tracks in order, with their positions. Page through long playlists with offset."""
    pid = _id(playlist_id, "playlist")
    fields = "items(is_local,item(name,uri,type,artists(name),album(name),show(name))),next,total"

    async def fetch(size: int, page_offset: int) -> dict:
        return await _spotify("playlist_items", pid, fields=fields, limit=size, offset=page_offset)

    entries, total, next_offset = await _collect(fetch, limit, PAGE_MAX, offset)
    items, local = _playlist_items(entries)
    tracks = [PlaylistTrack(**_track(item).model_dump(), position=offset + index) for index, item in items]
    return _result(PlaylistTracks(tracks=tracks, total=total, next_offset=next_offset, skipped_local_files=local))


@mcp.tool(title="Create playlist", annotations=_changes(), structured_output=False)
async def create_playlist(
    name: Annotated[str, Field(description="Name for the new playlist")],
    track_uris: Annotated[list[str], Field(description="Spotify track URIs to add, in order")] = [],
    description: str = "",
    public: Annotated[bool, Field(description="Show the playlist on the user's profile (default: private)")] = False,
) -> str:
    """Create a playlist in the user's account and fill it with tracks."""
    uris = _unique(_uri(u, ("track", "episode")) for u in track_uris)
    playlist = await _spotify("current_user_playlist_create", name=name, public=public, description=description)
    added = 0
    try:
        for batch in _chunks(uris, PLAYLIST_WRITE_MAX):
            await _spotify("playlist_add_items", playlist["id"], batch)
            added += len(batch)
    except SpotifyError as e:
        raise SpotifyError(
            f"Created playlist '{name}' (ID: {playlist['id']}) but adding tracks stopped after "
            f"{added} of {len(uris)}: {e}"
        ) from e
    visibility = "public" if public else "private"
    url = playlist["external_urls"]["spotify"]
    return f"Created {visibility} playlist '{name}' with {len(uris)} track(s).\nID: {playlist['id']}\n{url}"


@mcp.tool(title="Add to playlist", annotations=_changes(idempotent=True), structured_output=False)
async def add_to_playlist(playlist_id: PlaylistRef, track_uris: TrackUris) -> str:
    """Add tracks to the end of an existing playlist. Tracks already in it are skipped."""
    pid = _id(playlist_id, "playlist")
    uris = _unique(_uri(u, ("track", "episode")) for u in track_uris)
    existing = set(await _playlist_slots(pid))
    new = [u for u in uris if u not in existing]
    if not new:
        return f"All {len(uris)} track(s) are already in the playlist; nothing added."
    for batch in _chunks(new, PLAYLIST_WRITE_MAX):
        await _spotify("playlist_add_items", pid, batch)
    skipped = len(uris) - len(new)
    if skipped:
        return f"Added {len(new)}/{len(uris)} tracks ({skipped} already in playlist, skipped)."
    return f"Added {len(new)} tracks."


@mcp.tool(title="Remove from playlist", annotations=_changes(destructive=True, idempotent=True), structured_output=False)
async def remove_from_playlist(playlist_id: PlaylistRef, track_uris: TrackUris) -> str:
    """Remove tracks from a playlist. Removes every copy of each track; to drop only extra copies,
    use remove_playlist_duplicates instead."""
    pid = _id(playlist_id, "playlist")
    uris = _unique(_uri(u, ("track", "episode")) for u in track_uris)
    counts = Counter(await _playlist_slots(pid))
    present = [u for u in uris if counts[u]]
    missing = [u for u in uris if not counts[u]]
    if not present:
        return f"None of the {len(uris)} track(s) are in the playlist; nothing removed."
    for batch in _chunks(present, PLAYLIST_WRITE_MAX):
        await _spotify("playlist_remove_all_occurrences_of_items", pid, batch)
    copies = sum(counts[u] for u in present)
    message = f"Removed {len(present)} track(s)"
    message += f" ({copies} copies in total)." if copies > len(present) else "."
    if missing:
        message += f" {len(missing)} weren't in the playlist: {', '.join(missing)}"
    return message


@mcp.tool(title="Update playlist details", annotations=_changes(idempotent=True), structured_output=False)
async def update_playlist_details(
    playlist_id: PlaylistRef,
    name: str | None = None,
    description: str | None = None,
    public: bool | None = None,
) -> str:
    """Rename a playlist, change its description, or make it public/private. Pass only what should change."""
    if name is None and description is None and public is None:
        raise ToolError("Nothing to change: pass name, description or public.")
    await _spotify("playlist_change_details", _id(playlist_id, "playlist"), name=name, public=public, description=description)
    changes = [
        f"name to '{name}'" if name is not None else "",
        "description" if description is not None else "",
        f"visibility to {'public' if public else 'private'}" if public is not None else "",
    ]
    return f"Updated the playlist's {', '.join(c for c in changes if c)}."


@mcp.tool(title="Reorder playlist", annotations=_changes(), structured_output=False)
async def reorder_playlist(
    playlist_id: PlaylistRef,
    range_start: Annotated[int, Field(ge=0, description="Position of the first item to move")],
    insert_before: Annotated[int, Field(ge=0, description="Position to move the items in front of; use the playlist's length to move them to the end")],
    range_length: Annotated[int, Field(ge=1, description="How many consecutive items to move")] = 1,
) -> str:
    """Move one or more consecutive items within a playlist. Positions are 0-based, as returned by get_playlist_tracks.
    Example: move the item at position 5 to the top with range_start=5, insert_before=0."""
    await _spotify(
        "playlist_reorder_items", _id(playlist_id, "playlist"),
        range_start=range_start, insert_before=insert_before, range_length=range_length,
    )
    return f"Moved {range_length} item(s) from position {range_start} to before position {insert_before}."


class DeleteConfirmation(BaseModel):
    confirm: bool = Field(description="Remove this playlist from your Spotify library?")


def _can_elicit(ctx: Context) -> bool:
    elicitation = ctx.client_capabilities.elicitation if ctx.client_capabilities else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


async def _confirm_delete(playlist_id: str, ctx: Context) -> Elicit[DeleteConfirmation] | DeleteConfirmation:
    """Ask the user directly when the client supports it; otherwise rely on the model having asked."""
    if not _can_elicit(ctx):
        return DeleteConfirmation(confirm=True)
    playlist = await _spotify("playlist", _id(playlist_id, "playlist"), fields="name")
    return Elicit(f"Remove the playlist “{playlist['name']}” from your Spotify library?", DeleteConfirmation)


@mcp.tool(title="Delete playlist", annotations=_changes(destructive=True, idempotent=True), structured_output=False)
async def delete_playlist(
    playlist_id: PlaylistRef,
    confirmation: Annotated[ElicitationResult[DeleteConfirmation], Resolve(_confirm_delete)],
) -> str:
    """Remove a playlist from the user's library; for playlists they own, this deletes it.
    Always confirm with the user before calling this. Their app may also ask them to confirm."""
    if not (isinstance(confirmation, AcceptedElicitation) and confirmation.data.confirm):
        return "The user didn't confirm, so the playlist was kept."
    await _spotify("_delete", "me/library", uris=f"spotify:playlist:{_id(playlist_id, 'playlist')}")
    return "Playlist removed from the library."


_VERSION_SUFFIX = re.compile(r"\s*(?:[(\[][^)\]]*[)\]]|\s-\s.*)$")


def _base_title(name: str) -> str:
    """'Song - Remastered 2011' and 'Song (Live)' both become 'song'."""
    previous = None
    while previous != name:
        previous, name = name, _VERSION_SUFFIX.sub("", name)
    return " ".join(name.casefold().split())


def _playlist_issues(
    playlist_id: str, name: str, entries: list[dict | None], unplayable: set[int]
) -> PlaylistIssues:
    positions: dict[str, list[int]] = defaultdict(list)
    tracks: dict[str, PlaylistTrack] = {}
    by_title: dict[tuple[str, str], dict[str, PlaylistTrack]] = defaultdict(dict)
    items, local = _playlist_items(entries)
    issues = PlaylistIssues(playlist_id=playlist_id, name=name, local_files=local)
    for index, item in items:
        track = PlaylistTrack(**_track(item).model_dump(), position=index)
        positions[track.uri].append(index)
        tracks.setdefault(track.uri, track)
        if index in unplayable:
            issues.unavailable.append(track)
        title = _base_title(track.name)
        if title and item.get("type", "track") == "track":
            by_title[(title, (track.artists or [""])[0].casefold())].setdefault(track.uri, track)
    issues.duplicates = [
        DuplicateTrack(name=tracks[uri].name, artists=tracks[uri].artists, uri=uri, positions=found)
        for uri, found in positions.items()
        if len(found) > 1
    ]
    issues.likely_duplicates = [
        LikelyDuplicate(name=title, versions=list(versions.values()))
        for (title, _), versions in by_title.items()
        if len(versions) > 1
    ]
    return issues


@mcp.tool(title="Find playlist issues", annotations=READ_ONLY)
async def find_playlist_issues(
    ctx: Context,
    playlist_ids: Annotated[
        list[str] | None,
        Field(description="Playlists to check (IDs, URIs or URLs); default: every playlist the user owns or collaborates on"),
    ] = None,
) -> Annotated[CallToolResult, PlaylistAudit]:
    """Check playlists for duplicate tracks, different versions of the same song, tracks that are no longer
    playable, and local files. Only playlists with issues are listed. Checking every playlist can take a while;
    if Spotify stops it partway, the result says so and covers the playlists checked until then."""
    if playlist_ids:
        targets = [(pid, None) for pid in _unique(_id(p, "playlist") for p in playlist_ids)]
    else:
        me = await _spotify("current_user")

        async def fetch(size: int, offset: int) -> dict:
            return await _spotify("current_user_playlists", limit=size, offset=offset)

        playlists, _, _ = await _collect(fetch, 1000, PAGE_MAX)
        targets = [
            (p["id"], p["name"]) for p in playlists
            if p and ((p.get("owner") or {}).get("id") == me["id"] or p.get("collaborative"))
        ]
    audit = PlaylistAudit(playlists_checked=0, playlists_with_issues=[])
    for done, (pid, name) in enumerate(targets):
        await ctx.report_progress(done, len(targets), f"Checking {name or pid}")
        try:
            issues = await _check_playlist(pid, name)
        except SpotifyError as e:
            if not e.fatal:
                audit.skipped.append(f"{name or pid}: {e}")
                continue
            if not audit.playlists_checked and not audit.skipped:
                raise
            # Keep what's been found rather than losing a long audit to, say, a rate limit.
            audit.stopped_early = f"Stopped at playlist {done + 1} of {len(targets)} ({name or pid}): {e}"
            break
        audit.playlists_checked += 1
        if issues.duplicates or issues.likely_duplicates or issues.unavailable or issues.local_files:
            audit.playlists_with_issues.append(issues)
    return _result(audit)


async def _check_playlist(playlist_id: str, name: str | None) -> PlaylistIssues:
    if name is None:
        name = (await _spotify("playlist", playlist_id, fields="name"))["name"]
    entries = await _playlist_entries(playlist_id, "items(is_local,item(name,uri,type,artists(name),album(name))),next,total")
    # is_playable needs a market, but a market also turns on track relinking, which swaps in
    # URIs the other playlist tools never see. So it's read separately and matched by position.
    availability = await _playlist_entries(playlist_id, "items(item(is_playable)),next,total", market="from_token")
    unplayable = {i for i, entry in enumerate(availability) if (_entry_item(entry) or {}).get("is_playable") is False}
    return _playlist_issues(playlist_id, name, entries, unplayable)


@mcp.tool(title="Remove playlist duplicates", annotations=_changes(destructive=True, idempotent=True), structured_output=False)
async def remove_playlist_duplicates(playlist_id: PlaylistRef) -> str:
    """Remove extra copies of tracks that appear more than once in a playlist, keeping the first copy
    in its place. Only exact duplicates (same URI) are touched."""
    pid = _id(playlist_id, "playlist")
    slots = await _playlist_slots(pid)
    counts = Counter(u for u in slots if u)
    duplicated = sorted(u for u, n in counts.items() if n > 1)
    if not duplicated:
        return "No duplicate tracks found."
    # Spotify can only remove every copy of a track. So for up to 100 duplicated tracks at a time,
    # remove every copy and put the first one back, before touching the next batch.
    deduplicated: set[str] = set()
    for batch in _chunks(duplicated, PLAYLIST_WRITE_MAX):
        try:
            await _spotify("playlist_remove_all_occurrences_of_items", pid, batch)
        except SpotifyError as e:
            if not deduplicated:
                raise
            raise SpotifyError(
                f"Removed the extra copies of {len(deduplicated)} of {len(duplicated)} duplicated track(s), "
                f"then stopped; the rest are unchanged. ({e})"
            ) from e
        deduplicated.update(batch)
        runs = _first_copy_runs(slots, deduplicated, set(batch))
        restored = 0
        try:
            for position, uris in runs:
                await _spotify("playlist_add_items", pid, uris, position=position)
                restored += len(uris)
        except SpotifyError as e:
            lost = [uri for _, uris in runs for uri in uris][restored:]
            raise SpotifyError(
                f"Removed the duplicates, but couldn't put back one copy of {len(lost)} track(s): {', '.join(lost)}. "
                f"Add them back with add_to_playlist. ({e})"
            ) from e
    removed = sum(counts[u] - 1 for u in duplicated)
    return f"Removed {removed} extra copies of {len(duplicated)} track(s), keeping the first copy of each in place."


def _first_copy_runs(slots: list[str | None], deduplicated: set[str], batch: set[str]) -> list[tuple[int, list[str]]]:
    """Where to put back the first copy of each track in `batch` after removing every copy of it.

    `slots` is the playlist as it was; `deduplicated` holds the tracks whose extra copies are gone,
    `batch` included. Returns (position, uris) runs of consecutive positions, lowest first.
    Inserting them in that order rebuilds the playlist with only the first copy of those tracks,
    with removed items and local files (None) still counted, as they stay where they are.
    """
    target, seen = [], set()
    for uri in slots:
        if uri in deduplicated and uri in seen:
            continue
        seen.add(uri)
        target.append(uri)
    runs: list[tuple[int, list[str]]] = []
    for position, uri in enumerate(target):
        if uri not in batch:
            continue
        if runs and runs[-1][0] + len(runs[-1][1]) == position and len(runs[-1][1]) < PLAYLIST_WRITE_MAX:
            runs[-1][1].append(uri)
        else:
            runs.append((position, [uri]))
    return runs


# --- Playback ---

@mcp.tool(title="Get current playback", annotations=READ_ONLY)
async def get_current_playing() -> Annotated[CallToolResult, PlaybackState]:
    """Get what's playing (track or podcast episode), on which device, and the shuffle/repeat state.
    Useful context for picking music."""
    playback = await _spotify("current_playback", additional_types="episode")
    if not playback:
        return _result(PlaybackState(is_playing=False))
    item = playback.get("item")
    device = playback.get("device")
    progress = playback.get("progress_ms")
    return _result(PlaybackState(
        is_playing=playback.get("is_playing", False),
        item=_track(item) if item else None,
        artist_uris=[a["uri"] for a in (item or {}).get("artists") or [] if a],
        device=_device(device) if device else None,
        context_uri=(playback.get("context") or {}).get("uri"),
        progress_seconds=progress // 1000 if progress is not None else None,
        shuffle=playback.get("shuffle_state"),
        repeat=playback.get("repeat_state"),
    ))


@mcp.tool(title="Get queue", annotations=READ_ONLY)
async def get_queue() -> Annotated[CallToolResult, QueueState]:
    """Get the playback queue: what's playing now and what's coming up next."""
    queue = await _spotify("queue") or {}  # spotipy returns None for an empty body
    current = queue.get("currently_playing")
    upcoming = [_track(t) for t in queue.get("queue") or [] if t]
    return _result(QueueState(currently_playing=_track(current) if current else None, queue=upcoming))


@mcp.tool(title="List devices", annotations=READ_ONLY)
async def list_devices() -> Annotated[CallToolResult, DeviceList]:
    """List the devices Spotify can play on (phones, computers, speakers) and which one is active."""
    result = await _spotify("devices")
    return _result(DeviceList(devices=[_device(d) for d in result.get("devices") or [] if d]))


@mcp.tool(title="Transfer playback", annotations=_changes(idempotent=True), structured_output=False)
async def transfer_playback(
    device_id: Annotated[str, Field(description="Device to move playback to (from list_devices)")],
    play: Annotated[bool, Field(
        description="Start playing on the new device; if false, it keeps playing or stays paused as before",
    )] = True,
) -> str:
    """Move playback to another device. Requires Spotify Premium."""
    await _spotify("transfer_playback", device_id, force_play=play)
    if play:
        return "Playback moved to the device and is playing."
    return "Playback moved to the device; it's playing or paused as it was before."


_CONTEXT_KINDS = ("album", "playlist", "artist", "show")


@mcp.tool(title="Play now", annotations=_changes(), structured_output=False)
async def play_now(
    uris: Annotated[list[str], Field(
        min_length=1,
        description="Either one album, playlist, artist or show URI, or a list of track/episode URIs to play in order",
    )],
    device_id: DeviceId = None,
) -> str:
    """Start playing something right away, replacing what's playing. Requires Spotify Premium.
    To play after the current track instead, use queue_tracks."""
    normalized = [_uri(u, ("track", "episode") + _CONTEXT_KINDS) for u in uris]
    kinds = {uri.split(":")[1] for uri in normalized}
    if kinds <= {"track", "episode"}:
        await _spotify("start_playback", device_id=device_id, uris=normalized)
        return f"Playing {len(normalized)} item(s)."
    if len(normalized) == 1:
        await _spotify("start_playback", device_id=device_id, context_uri=normalized[0])
        return f"Playing {normalized[0]}."
    raise ToolError("Pass either a single album/playlist/artist/show URI, or only track/episode URIs.")


@mcp.tool(title="Queue tracks", annotations=_changes(), structured_output=False)
async def queue_tracks(
    track_uris: Annotated[list[str], Field(min_length=1, description="Spotify track or episode URIs to queue, in order")],
    device_id: DeviceId = None,
) -> str:
    """Add specific tracks or podcast episodes to the playback queue. Requires Spotify Premium.
    Use this when you already have URIs; prefer it over queue_recommendations for known tracks."""
    uris = _unique(_uri(u, ("track", "episode")) for u in track_uris)
    queued, failed = await _queue(uris, device_id)
    summary = f"Queued {queued} track(s)."
    if failed:
        summary += f" {len(failed)} failed: {', '.join(failed)}"
    return summary


async def _queue(uris: list[str], device_id: str | None) -> tuple[int, list[str]]:
    """Queue URIs in order. Skips ones Spotify rejects; stops on errors that affect every item."""
    queued, failed = 0, []
    for uri in uris:
        try:
            await _spotify("add_to_queue", uri, device_id=device_id)
            queued += 1
        except SpotifyError as e:
            if e.fatal:
                if queued:
                    raise SpotifyError(f"{e} (Queued {queued} of {len(uris)} before stopping.)") from e
                raise
            log.warning("Failed to queue %s: %s", uri, e)
            failed.append(uri)
    return queued, failed


async def _tracks_by_artists(artists: list[str], count: int) -> list[dict]:
    """About `count` tracks spread evenly over the artists, searched one artist at a time."""
    artists = artists[:count]  # each artist gets at least one track
    per_artist = -(-count // len(artists))
    queries = [f'artist:"{name}"' for name in (a.replace('"', "") for a in artists)]
    slots = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)  # 50 searches at once would hit the rate limit

    async def search(query: str) -> list[dict]:
        async with slots:
            return await _search(query, "track", per_artist)

    tasks = [asyncio.create_task(search(q)) for q in queries]
    try:
        results = await asyncio.gather(*tasks)
    finally:
        # One failed search fails the tool, so stop the rest instead of letting them page and retry.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    picked: dict[str, dict] = {}
    for rank in range(per_artist):
        for tracks in results:
            if rank < len(tracks):
                picked.setdefault(tracks[rank]["uri"], tracks[rank])
    return list(picked.values())[:count]


@mcp.tool(title="Search and queue", annotations=_changes(), structured_output=False)
async def queue_recommendations(
    artists: Annotated[list[str] | None, Field(
        description="Artists whose sound fits the request; tracks are spread evenly across them. "
        "Preferred over query for moods and vibes.",
    )] = None,
    query: Annotated[str | None, Field(
        description="A search query instead of artists, e.g. 'genre:chillwave'. "
        "Omit both to search for the currently playing track.",
    )] = None,
    count: Annotated[int, Field(ge=1, le=50, description="Number of tracks to queue (1-50)")] = 10,
    device_id: DeviceId = None,
) -> str:
    """Find tracks and add them to the playback queue in one step. Requires Spotify Premium.
    If you already know which tracks to queue, use search_tracks and then queue_tracks; it's more precise.
    STRATEGY: Spotify search matches text literally against track and artist names, so mood adjectives
    (e.g. 'dreamy', 'melancholic') match song titles, not audio style. Reason about which artists fit the mood
    (call get_current_playing for context) and pass them as artists."""
    if artists:
        label = ", ".join(artists)
        tracks = await _tracks_by_artists(artists, count)
    else:
        if not query:
            playback = await _spotify("currently_playing", additional_types="episode")
            item = (playback or {}).get("item")
            if not item:
                return "No artists or query provided and nothing is currently playing. Provide artists or a query."
            if item.get("type") == "episode":
                return "No artists or query provided, and a podcast is playing, not a track. Provide artists or a query."
            query = " ".join([item["name"], *_names(item.get("artists"))[:1]])
            log.info("queue_recommendations: no query provided, using current track: %r", query)
        label = query
        tracks = await _search(query, "track", count)
    if not tracks:
        return f"No tracks found for: {label}"
    queued, failed = await _queue([t["uri"] for t in tracks], device_id)
    titles = [f"{t['name']} — {', '.join(_names(t.get('artists')))}" for t in tracks if t["uri"] not in failed]
    summary = f"Queued {queued} track(s) for '{label}':"
    if failed:
        summary += f" ({len(failed)} failed)"
    return summary + "\n\n" + "\n".join(f"{i}. {title}" for i, title in enumerate(titles, 1))


_ACTIONS = {
    "play": ("start_playback", "resumed playback"),
    "pause": ("pause_playback", "paused"),
    "skip_next": ("next_track", "skipped to the next track"),
    "skip_previous": ("previous_track", "went back to the previous track"),
}


@mcp.tool(title="Control playback", annotations=_changes(), structured_output=False)
async def control_playback(
    action: Annotated[
        Literal["play", "pause", "skip_next", "skip_previous"] | None,
        Field(description="play resumes playback; to play something specific, use play_now"),
    ] = None,
    shuffle: bool | None = None,
    repeat: Annotated[
        Literal["off", "track", "context"] | None,
        Field(description="track repeats the current track; context repeats the album or playlist"),
    ] = None,
    volume_percent: Annotated[int | None, Field(ge=0, le=100)] = None,
    seek_seconds: Annotated[int | None, Field(ge=0, description="Jump to this point in the current item")] = None,
    device_id: DeviceId = None,
) -> str:
    """Control playback: play/pause/skip, shuffle, repeat, volume and seeking. Pass any combination.
    Requires Spotify Premium."""
    steps: list[tuple[str, tuple, str]] = []
    if shuffle is not None:
        steps.append(("shuffle", (shuffle,), f"shuffle {'on' if shuffle else 'off'}"))
    if repeat is not None:
        steps.append(("repeat", (repeat,), f"repeat {repeat}"))
    if volume_percent is not None:
        steps.append(("volume", (volume_percent,), f"volume {volume_percent}%"))
    if action is not None:
        method, label = _ACTIONS[action]
        steps.append((method, (), label))
    if seek_seconds is not None:
        steps.append(("seek_track", (seek_seconds * 1000,), f"jumped to {seek_seconds // 60}:{seek_seconds % 60:02d}"))
    if not steps:
        raise ToolError("Nothing to do: pass an action or a setting.")
    done: list[str] = []
    for method, args, label in steps:
        try:
            await _spotify(method, *args, device_id=device_id)
        except SpotifyError as e:
            if done:
                raise SpotifyError(f"{e} (Already done: {', '.join(done)}.)") from e
            raise
        done.append(label)
    return f"Done: {', '.join(done)}."


# --- Prompts ---

@mcp.prompt(title="Music for a mood")
def vibe(mood: str, destination: str = "queue") -> str:
    """Find music that fits a mood and queue it, or save it as a playlist (destination: queue or playlist)."""
    if destination.strip().lower() == "playlist":
        finish = (
            "Search each artist with search_tracks ('artist:Name', one artist per search), pick about 15 tracks "
            "that fit, show me the tracklist, and create the playlist once I confirm."
        )
    else:
        finish = "Queue about 10 tracks with queue_recommendations, passing the artists. Don't ask me first."
    return f"""Find music that fits this mood: {mood}

1. Get context: call get_current_playing, get_recently_played and get_my_top_artists (short_term).
2. Using your own music knowledge, pick 4-8 artists whose sound fits the mood and my taste. Don't search \
for the mood words themselves; they only match song titles.
3. {finish}"""


def main() -> None:
    _configure_logging()
    mcp.run()


if __name__ == "__main__":
    main()
