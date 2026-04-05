import asyncio
import logging
import os
import random
import time
import traceback

import mcp.server.stdio
import mcp.types as types
import spotipy
from dotenv import load_dotenv
from mcp.server import Server
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

# --- Logging setup ---
_log_path = os.path.join(os.path.dirname(__file__), "mcp-spotify.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(_log_path, encoding="utf-8")],
)
log = logging.getLogger("mcp-spotify")

_search_cache: dict[tuple, tuple] = {}  # key -> (result, timestamp)
_CACHE_TTL = 300  # seconds

# --- Spotify client setup ---
log.info("Initialising Spotify OAuth client")
try:
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
            scope=" ".join([
                "playlist-modify-public",
                "playlist-modify-private",
                "playlist-read-private",
                "user-read-private",
                "user-read-currently-playing",
                "user-modify-playback-state",
                "user-top-read",
            ]),
        )
    )
    log.info("Spotify OAuth client ready")
except Exception:
    log.critical("Failed to initialise Spotify OAuth client\n%s", traceback.format_exc())
    raise

# --- MCP server setup ---
server = Server("mcp-spotify")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_tracks",
            description="Search Spotify for tracks by a query string. Returns track names, artists, and URIs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, e.g. 'sad indie 2020' or 'artist:Radiohead'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results to return (1–50, default 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="create_playlist",
            description="Create a Spotify playlist and populate it with tracks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the new playlist"},
                    "track_uris": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of Spotify track URIs",
                    },
                    "description": {"type": "string", "description": "Optional playlist description", "default": ""},
                    "public": {"type": "boolean", "description": "Whether the playlist is public (default true)", "default": True},
                },
                "required": ["name", "track_uris"],
            },
        ),
        types.Tool(
            name="get_current_playing",
            description=(
                "Get the track currently playing on Spotify. "
                "Returns the track name, artist, and URI. "
                "Useful as an automatic seed for queue_recommendations."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="queue_recommendations",
            description=(
                "Search for tracks matching a query and add them to the Spotify playback queue. "
                "Requires Spotify Premium. "
                "If no query is provided, automatically builds one from the currently playing track. "
                "QUERY STRATEGY: Spotify search matches text literally against track and artist names — "
                "mood adjectives (e.g. 'dreamy', 'melancholic') will match song titles, not audio style. "
                "Use mood and vibe to reason about which artists belong in the queue, "
                "then build the query using artist: operators: 'artist:Folamour OR artist:Floating Points'. "
                "Call get_current_playing first to get context, then use your knowledge of the music scene "
                "to select artists whose style matches the requested mood or vibe."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Descriptive search query encoding vibe, genre, mood, era, or artist references. "
                            "Omit to auto-generate from the currently playing track."
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of tracks to queue (1–50, default 10)",
                        "default": 10,
                    },
                },
            },
        ),
        types.Tool(
            name="get_my_top_tracks",
            description="Get the user's most listened-to tracks. Useful as seeds for personalized recommendations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of tracks to return (1–50, default 10)", "default": 10},
                    "time_range": {
                        "type": "string",
                        "description": "'short_term' (last 4 weeks), 'medium_term' (last 6 months), 'long_term' (all time). Default: medium_term.",
                        "enum": ["short_term", "medium_term", "long_term"],
                        "default": "medium_term",
                    },
                },
            },
        ),
        types.Tool(
            name="get_my_top_artists",
            description="Get the user's most listened-to artists. Useful as seeds for personalized recommendations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of artists to return (1–50, default 10)", "default": 10},
                    "time_range": {
                        "type": "string",
                        "description": "'short_term' (last 4 weeks), 'medium_term' (last 6 months), 'long_term' (all time). Default: medium_term.",
                        "enum": ["short_term", "medium_term", "long_term"],
                        "default": "medium_term",
                    },
                },
            },
        ),
        types.Tool(
            name="list_my_playlists",
            description="List the current user's Spotify playlists. Returns names, IDs, and track counts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of playlists to return (1–50, default 20)", "default": 20},
                },
            },
        ),
    ]


_handlers = {}


def tool_handler(name: str):
    def decorator(fn):
        _handlers[name] = fn
        return fn
    return decorator


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name not in _handlers:
        log.warning("Unknown tool called: %s", name)
        raise ValueError(f"Unknown tool: {name}")
    log.info("Tool call started: %s | args: %s", name, arguments)
    t0 = time.perf_counter()
    try:
        result = await _handlers[name](arguments)
        log.info("Tool call finished: %s | %.2fs", name, time.perf_counter() - t0)
        return result
    except Exception:
        log.error("Tool call failed: %s | %.2fs\n%s", name, time.perf_counter() - t0, traceback.format_exc())
        raise


# --- Helpers ---

async def _spotify(fn, *args, **kwargs):
    """Call a Spotify API function with exponential backoff on 429 rate limits."""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except spotipy.SpotifyException as e:
            if e.http_status != 429 or attempt == max_retries - 1:
                raise
            retry_after = int((e.headers or {}).get("Retry-After", 2 ** attempt))
            wait = retry_after + random.uniform(0, 1)
            log.warning("Rate limited (429), retrying in %.1fs (attempt %d/%d)", wait, attempt + 1, max_retries)
            await asyncio.sleep(wait)


def _format_track(i: int, track: dict) -> str:
    name = track["name"]
    artists = ", ".join(a["name"] for a in track["artists"])
    uri = track["uri"]
    return f"{i}. {name} — {artists}\n   URI: {uri}"


# --- Tool handlers ---

@tool_handler("search_tracks")
async def search_tracks(args: dict) -> list[types.TextContent]:
    query = args["query"]
    limit = args.get("limit", 10)
    cache_key = ("search", query, limit)
    now = time.monotonic()

    cached = _search_cache.get(cache_key)
    if cached and now - cached[1] < _CACHE_TTL:
        log.debug("search_tracks cache hit | query=%r", query)
        results = cached[0]
    else:
        log.debug("search_tracks cache miss | query=%r limit=%s", query, limit)
        t0 = time.perf_counter()
        try:
            results = await _spotify(sp.search, q=query, type="track", limit=limit)
        except Exception:
            log.error("sp.search failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
            raise
        log.debug("sp.search returned in %.2fs", time.perf_counter() - t0)
        _search_cache[cache_key] = (results, now)

    tracks = results["tracks"]["items"]
    if not tracks:
        return [types.TextContent(type="text", text="No tracks found.")]

    lines = [_format_track(i, t) for i, t in enumerate(tracks, 1)]
    return [types.TextContent(type="text", text="\n\n".join(lines))]


@tool_handler("create_playlist")
async def create_playlist(args: dict) -> list[types.TextContent]:
    name = args["name"]
    track_uris = args["track_uris"]
    description = args.get("description", "")
    public = args.get("public", True)

    log.debug("Creating playlist name=%r public=%s tracks=%d", name, public, len(track_uris))
    t0 = time.perf_counter()
    try:
        playlist = await _spotify(
            sp.current_user_playlist_create,
            name=name,
            public=public,
            description=description,
        )
    except Exception:
        log.error("sp.current_user_playlist_create failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.info("Playlist created id=%r (%.2fs)", playlist["id"], time.perf_counter() - t0)

    for i in range(0, len(track_uris), 100):
        batch = track_uris[i: i + 100]
        log.debug("Adding tracks batch %d–%d", i, i + len(batch))
        t0 = time.perf_counter()
        try:
            await _spotify(sp.playlist_add_items, playlist["id"], batch)
        except Exception:
            log.error("sp.playlist_add_items failed at offset %d (%.2fs)\n%s", i, time.perf_counter() - t0, traceback.format_exc())
            raise
        log.debug("Batch added (%.2fs)", time.perf_counter() - t0)

    url = playlist["external_urls"]["spotify"]
    return [types.TextContent(type="text", text=f"Playlist '{name}' created with {len(track_uris)} track(s).\n{url}")]


@tool_handler("get_current_playing")
async def get_current_playing(args: dict) -> list[types.TextContent]:
    t0 = time.perf_counter()
    try:
        playback = await _spotify(sp.currently_playing)
    except Exception:
        log.error("sp.currently_playing failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.debug("sp.currently_playing returned in %.2fs", time.perf_counter() - t0)

    if not playback or not playback.get("item"):
        return [types.TextContent(type="text", text="Nothing is currently playing.")]

    track = playback["item"]
    name = track["name"]
    artists = ", ".join(a["name"] for a in track["artists"])
    uri = track["uri"]
    artist_uri = track["artists"][0]["uri"]
    is_playing = playback.get("is_playing", False)
    status = "Playing" if is_playing else "Paused"

    return [types.TextContent(type="text", text=f"{status}: {name} — {artists}\nTrack URI: {uri}\nArtist URI: {artist_uri}")]



@tool_handler("queue_recommendations")
async def queue_recommendations(args: dict) -> list[types.TextContent]:
    query = args.get("query")
    count = min(args.get("count", 10), 50)

    # If no query, build one from the currently playing track
    if not query:
        try:
            playback = await _spotify(sp.currently_playing)
            if playback and playback.get("item"):
                track = playback["item"]
                name = track["name"]
                artist = track["artists"][0]["name"]
                query = f"{name} {artist}"
                log.info("queue_recommendations: no query provided, using current track: %r", query)
            else:
                return [types.TextContent(type="text", text="No query provided and nothing is currently playing. Please provide a search query.")]
        except Exception:
            log.warning("Could not fetch currently playing track\n%s", traceback.format_exc())
            return [types.TextContent(type="text", text="No query provided and could not fetch the currently playing track.")]

    log.debug("queue_recommendations | query=%r count=%s", query, count)
    t0 = time.perf_counter()
    try:
        results = await _spotify(sp.search, q=query, type="track", limit=count)
    except Exception:
        log.error("sp.search failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.debug("sp.search returned in %.2fs", time.perf_counter() - t0)

    tracks = results["tracks"]["items"]
    if not tracks:
        return [types.TextContent(type="text", text=f"No tracks found for query: {query!r}")]

    seen_uris: set[str] = set()
    unique_tracks = []
    for t in tracks:
        if t["uri"] not in seen_uris:
            seen_uris.add(t["uri"])
            unique_tracks.append(t)
    tracks = unique_tracks

    queued = []
    failed = []
    for track in tracks:
        try:
            await _spotify(sp.add_to_queue, track["uri"])
            queued.append(f"{track['name']} — {', '.join(a['name'] for a in track['artists'])}")
        except spotipy.SpotifyException as e:
            if e.http_status == 403:
                return [types.TextContent(type="text", text="Spotify Premium is required to add tracks to the queue.")]
            failed.append(track["name"])
            log.warning("Failed to queue %s: %s", track["uri"], e)

    lines = [f"{i}. {t}" for i, t in enumerate(queued, 1)]
    summary = f"Queued {len(queued)} track(s) for '{query}':"
    if failed:
        summary += f" ({len(failed)} failed)"
    return [types.TextContent(type="text", text=summary + "\n\n" + "\n".join(lines))]


@tool_handler("get_my_top_tracks")
async def get_my_top_tracks(args: dict) -> list[types.TextContent]:
    limit = args.get("limit", 10)
    time_range = args.get("time_range", "medium_term")

    t0 = time.perf_counter()
    try:
        results = await _spotify(sp.current_user_top_tracks, limit=limit, time_range=time_range)
    except Exception:
        log.error("sp.current_user_top_tracks failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.debug("sp.current_user_top_tracks returned in %.2fs", time.perf_counter() - t0)

    tracks = results["items"]
    if not tracks:
        return [types.TextContent(type="text", text="No top tracks found.")]

    lines = [_format_track(i, t) for i, t in enumerate(tracks, 1)]
    label = {"short_term": "last 4 weeks", "medium_term": "last 6 months", "long_term": "all time"}[time_range]
    return [types.TextContent(type="text", text=f"Your top tracks ({label}):\n\n" + "\n\n".join(lines))]


@tool_handler("get_my_top_artists")
async def get_my_top_artists(args: dict) -> list[types.TextContent]:
    limit = args.get("limit", 10)
    time_range = args.get("time_range", "medium_term")

    t0 = time.perf_counter()
    try:
        results = await _spotify(sp.current_user_top_artists, limit=limit, time_range=time_range)
    except Exception:
        log.error("sp.current_user_top_artists failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.debug("sp.current_user_top_artists returned in %.2fs", time.perf_counter() - t0)

    artists = results["items"]
    if not artists:
        return [types.TextContent(type="text", text="No top artists found.")]

    lines = []
    for i, artist in enumerate(artists, 1):
        genres = ", ".join(artist["genres"][:3]) if artist["genres"] else "—"
        lines.append(f"{i}. {artist['name']} (genres: {genres})\n   URI: {artist['uri']}")

    label = {"short_term": "last 4 weeks", "medium_term": "last 6 months", "long_term": "all time"}[time_range]
    return [types.TextContent(type="text", text=f"Your top artists ({label}):\n\n" + "\n\n".join(lines))]


@tool_handler("list_my_playlists")
async def list_my_playlists(args: dict) -> list[types.TextContent]:
    limit = args.get("limit", 20)

    t0 = time.perf_counter()
    try:
        results = await _spotify(sp.current_user_playlists, limit=limit)
    except Exception:
        log.error("sp.current_user_playlists failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.debug("sp.current_user_playlists returned in %.2fs", time.perf_counter() - t0)

    playlists = results["items"]
    if not playlists:
        return [types.TextContent(type="text", text="No playlists found.")]

    lines = []
    for i, pl in enumerate(playlists, 1):
        name = pl["name"]
        total = pl["tracks"]["total"]
        pid = pl["id"]
        owner = pl["owner"]["display_name"]
        lines.append(f"{i}. {name} ({total} tracks) — owner: {owner}\n   ID: {pid}")

    return [types.TextContent(type="text", text=f"Your playlists:\n\n" + "\n\n".join(lines))]


# --- Entry point ---
async def main():
    log.info("MCP server starting (pid=%d)", os.getpid())
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        log.info("stdio streams open, entering run loop")
        await server.run(read_stream, write_stream, server.create_initialization_options())
    log.info("MCP server shut down")


if __name__ == "__main__":
    asyncio.run(main())
