# Spotify Web API Guidelines

You are helping build an MCP server that integrates with the Spotify Web API. Follow these rules at all times.

## API Reference

Always refer to the Spotify OpenAPI specification for endpoint paths, parameters, and response schemas:
https://developer.spotify.com/reference/web-api/open-api-schema.yaml

Do not guess endpoints or field names. If unsure whether an endpoint exists or is deprecated, fetch the spec.

## Authorization

This project uses the **Authorization Code flow** (secure backend). Spotipy handles token management via `SpotifyOAuth`. Never use the Implicit Grant flow (deprecated).

## Redirect URIs

The redirect URI is `http://127.0.0.1:8888/callback` — a local server started by `auth.py` catches the callback automatically. Never use `http://localhost` or wildcard URIs. `http://127.0.0.1` is the only allowed non-HTTPS exception.

## Scopes

Request only the minimum scopes needed. Current scopes in use:
- `playlist-modify-public` — create/edit public playlists
- `playlist-modify-private` — create/edit private playlists
- `playlist-read-private` — list user's playlists
- `user-read-private` — read user profile
- `user-read-currently-playing` — get currently playing track
- `user-modify-playback-state` — add to queue
- `user-top-read` — get top tracks/artists

Do not add new scopes without updating both `server.py` and `auth.py`.

## Rate Limits

All Spotify API calls go through the `_spotify()` helper in `server.py`, which implements exponential backoff with the `Retry-After` header on HTTP 429 responses. Never call `sp.*` methods directly with `asyncio.to_thread` — always use `_spotify()`.

## Deprecated Fields

On `GET /artists/{id}`, the `genres`, `followers`, and `popularity` fields are individually marked deprecated in the OpenAPI spec. Do not expose them in tool output.

## Deprecated Endpoints

Do not use deprecated endpoints. Known deprecated endpoints that must not be used:
- `GET /artists/{id}/related-artists`
- `GET /artists/{id}/top-tracks`
- `GET /recommendations`
- `POST /users/{user_id}/playlists` — use `sp.current_user_playlist_create()` (`POST /me/playlists`) instead
- `POST /playlists/{id}/tracks` — use `sp.playlist_add_items()` (`POST /playlists/{id}/items`) instead

## Error Handling

Handle HTTP error codes from the OpenAPI schema. Surface meaningful messages to the user. The 403 on queue endpoints means Spotify Premium is required — handle it explicitly.

## Caching

The `_search_cache` in `server.py` caches `search_tracks` results for 5 minutes. Do not cache other Spotify content beyond immediate use per the Spotify Developer Terms.

## Developer Terms

- Always attribute content to Spotify
- Do not use API data to train ML models
- Do not cache Spotify content beyond immediate use
