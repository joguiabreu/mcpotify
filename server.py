import asyncio
import logging
import os
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
# Log to a file (never stdout — that's the MCP stdio channel).
_log_path = os.path.join(os.path.dirname(__file__), "mcp-spotify.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(_log_path, encoding="utf-8")],
)
log = logging.getLogger("mcp-spotify")

_user_id: str | None = None
_search_cache: dict[tuple, str] = {}

# --- Spotify client setup ---
# SpotifyOAuth handles the Authorization Code flow.
# The first time this runs, it will open a browser for you to log in.
# After that, it caches the token in .cache so you don't need to re-auth.
log.info("Initialising Spotify OAuth client")
try:
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
            scope="playlist-modify-public playlist-modify-private user-read-private",
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
    """Tell MCP clients which tools this server exposes."""
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
            name="get_recommendations",
            description=(
                "Get Spotify track recommendations based on seed artists or tracks, "
                "and optional audio feature targets (mood parameters). "
                "Use search_tracks first to get seed URIs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "seed_tracks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Up to 5 Spotify track URIs or IDs to use as seeds",
                    },
                    "seed_artists": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Up to 5 Spotify artist URIs or IDs to use as seeds",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of tracks to return (1–100, default 20)",
                        "default": 20,
                    },
                    "target_energy": {
                        "type": "number",
                        "description": "Target energy level 0.0 (calm) to 1.0 (intense)",
                    },
                    "target_valence": {
                        "type": "number",
                        "description": "Target valence (mood) 0.0 (sad/dark) to 1.0 (happy/euphoric)",
                    },
                    "target_danceability": {
                        "type": "number",
                        "description": "Target danceability 0.0 (least) to 1.0 (most)",
                    },
                    "target_acousticness": {
                        "type": "number",
                        "description": "Target acousticness 0.0 (electric) to 1.0 (acoustic)",
                    },
                    "target_instrumentalness": {
                        "type": "number",
                        "description": "Target instrumentalness 0.0 (vocals) to 1.0 (instrumental)",
                    },
                    "target_tempo": {
                        "type": "number",
                        "description": "Target tempo in BPM, e.g. 90.0",
                    },
                    "target_popularity": {
                        "type": "integer",
                        "description": "Target popularity 0 (obscure) to 100 (mainstream)",
                    },
                },
            },
        ),
        types.Tool(
            name="create_playlist",
            description="Create a Spotify playlist and populate it with tracks. Use search_tracks first to get track URIs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the new playlist",
                    },
                    "track_uris": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of Spotify track URIs, e.g. ['spotify:track:abc123']",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional playlist description",
                        "default": "",
                    },
                    "public": {
                        "type": "boolean",
                        "description": "Whether the playlist is public (default true)",
                        "default": True,
                    },
                },
                "required": ["name", "track_uris"],
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
    """Route tool calls to the right handler."""
    if name not in _handlers:
        log.warning("Unknown tool called: %s", name)
        raise ValueError(f"Unknown tool: {name}")
    log.info("Tool call started: %s | args: %s", name, arguments)
    t0 = time.perf_counter()
    try:
        result = await _handlers[name](arguments)
        elapsed = time.perf_counter() - t0
        log.info("Tool call finished: %s | %.2fs", name, elapsed)
        return result
    except Exception:
        elapsed = time.perf_counter() - t0
        log.error("Tool call failed: %s | %.2fs\n%s", name, elapsed, traceback.format_exc())
        raise


@tool_handler("get_recommendations")
async def get_recommendations(args: dict) -> list[types.TextContent]:
    def to_id(uri: str) -> str:
        return uri.split(":")[-1] if ":" in uri else uri

    seed_tracks = [to_id(u) for u in args.get("seed_tracks", [])]
    seed_artists = [to_id(u) for u in args.get("seed_artists", [])]
    limit = args.get("limit", 20)

    audio_features = {
        k: args[k] for k in (
            "target_energy", "target_valence", "target_danceability",
            "target_acousticness", "target_instrumentalness",
            "target_tempo", "target_popularity",
        ) if k in args
    }

    log.debug(
        "recommendations request | seed_tracks=%s seed_artists=%s limit=%s features=%s",
        seed_tracks, seed_artists, limit, audio_features,
    )
    t0 = time.perf_counter()
    try:
        results = await asyncio.to_thread(
            sp.recommendations,
            seed_tracks=seed_tracks or None,
            seed_artists=seed_artists or None,
            limit=limit,
            **audio_features,
        )
    except Exception:
        log.error("sp.recommendations failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.debug("sp.recommendations returned in %.2fs", time.perf_counter() - t0)

    tracks = results["tracks"]
    if not tracks:
        log.info("recommendations: no tracks returned")
        return [types.TextContent(type="text", text="No recommendations found.")]

    lines = []
    for i, track in enumerate(tracks, 1):
        name = track["name"]
        artists = ", ".join(a["name"] for a in track["artists"])
        uri = track["uri"]
        lines.append(f"{i}. {name} — {artists}\n   URI: {uri}")

    return [types.TextContent(type="text", text="\n\n".join(lines))]


@tool_handler("search_tracks")
async def search_tracks(args: dict) -> list[types.TextContent]:
    query = args["query"]
    limit = args.get("limit", 10)
    cache_key = (query, limit)

    if cache_key not in _search_cache:
        log.debug("search_tracks cache miss | query=%r limit=%s", query, limit)
        t0 = time.perf_counter()
        try:
            results = await asyncio.to_thread(sp.search, q=query, type="track", limit=limit)
        except Exception:
            log.error("sp.search failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
            raise
        log.debug("sp.search returned in %.2fs", time.perf_counter() - t0)
        _search_cache[cache_key] = results
    else:
        log.debug("search_tracks cache hit | query=%r limit=%s", query, limit)

    tracks = _search_cache[cache_key]["tracks"]["items"]

    if not tracks:
        log.info("search_tracks: no tracks found for query=%r", query)
        return [types.TextContent(type="text", text="No tracks found.")]

    lines = []
    for i, track in enumerate(tracks, 1):
        name = track["name"]
        artists = ", ".join(a["name"] for a in track["artists"])
        uri = track["uri"]  # e.g. "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
        lines.append(f"{i}. {name} — {artists}\n   URI: {uri}")

    return [types.TextContent(type="text", text="\n\n".join(lines))]


@tool_handler("create_playlist")
async def create_playlist(args: dict) -> list[types.TextContent]:
    global _user_id
    name = args["name"]
    track_uris = args["track_uris"]
    description = args.get("description", "")
    public = args.get("public", True)

    # Step 1: get the current user's Spotify ID (cached after first call)
    if _user_id is None:
        log.debug("Fetching current Spotify user ID")
        t0 = time.perf_counter()
        try:
            _user_id = (await asyncio.to_thread(sp.current_user))["id"]
        except Exception:
            log.error("sp.current_user failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
            raise
        log.info("Got user ID=%r (%.2fs)", _user_id, time.perf_counter() - t0)

    # Step 2: create an empty playlist
    log.debug("Creating playlist name=%r public=%s tracks=%d", name, public, len(track_uris))
    t0 = time.perf_counter()
    try:
        playlist = await asyncio.to_thread(
            sp.user_playlist_create,
            user=_user_id,
            name=name,
            public=public,
            description=description,
        )
    except Exception:
        log.error("sp.user_playlist_create failed (%.2fs)\n%s", time.perf_counter() - t0, traceback.format_exc())
        raise
    log.info("Playlist created id=%r (%.2fs)", playlist["id"], time.perf_counter() - t0)

    # Step 3: add tracks (Spotify accepts max 100 per request)
    for i in range(0, len(track_uris), 100):
        batch = track_uris[i : i + 100]
        log.debug("Adding tracks batch %d–%d", i, i + len(batch))
        t0 = time.perf_counter()
        try:
            await asyncio.to_thread(sp.playlist_add_items, playlist["id"], batch)
        except Exception:
            log.error("sp.playlist_add_items failed at offset %d (%.2fs)\n%s", i, time.perf_counter() - t0, traceback.format_exc())
            raise
        log.debug("Batch added (%.2fs)", time.perf_counter() - t0)

    url = playlist["external_urls"]["spotify"]
    return [
        types.TextContent(
            type="text",
            text=f"Playlist '{name}' created with {len(track_uris)} track(s).\n{url}",
        )
    ]


# --- Entry point ---
async def main():
    log.info("MCP server starting (pid=%d)", os.getpid())
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        log.info("stdio streams open, entering run loop")
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )
    log.info("MCP server shut down")


if __name__ == "__main__":
    asyncio.run(main())
