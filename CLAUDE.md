# Spotify Web API Guidelines

You are helping build an MCP server that integrates with the Spotify Web API. Follow these rules at all times.

## API Reference

Always refer to the Spotify OpenAPI specification for endpoint paths, parameters, and response schemas:
https://developer.spotify.com/reference/web-api/open-api-schema.yaml

Do not guess endpoints or field names. If unsure whether an endpoint exists or is deprecated, fetch the spec.

## Authorization

This project uses the **Authorization Code flow** (secure backend). Spotipy handles token management via `SpotifyOAuth`. Never use the Implicit Grant flow (deprecated).

Login settings live in `spotify_auth.py`, shared by `server.py` and `auth.py`. The server's auth manager must never fall back to spotipy's interactive login: it would open a browser and read stdin, which is the MCP pipe, and hang. A missing, expired or under-scoped login raises `LoginRequired`, which `_spotify()` turns into a message telling the user to run `auth.py`.

Spotify refresh tokens expire 6 months after the user authorizes (`invalid_grant`), and refreshing doesn't extend that. `auth.py` stores `authorized_at` in `.cache` so the server can warn before expiry. The warning is appended to tool results by the `_login_expiry_notice` middleware, checked on every call; not in `INSTRUCTIONS`, which clients read once per connection. In that window, `auth.py` renews without `--force`. `has_valid_login()` always refreshes the token, since an unexpired access token doesn't show that the user revoked access.

## Redirect URIs

The redirect URI is `http://127.0.0.1:8888/callback` — a local server started by `auth.py` catches the callback automatically. Never use `http://localhost` or wildcard URIs. `http://127.0.0.1` is the only allowed non-HTTPS exception.

## Scopes

Request only the minimum scopes needed. The list lives in one place: `SCOPES` in `spotify_auth.py`, with a comment saying what each scope is for. Changing it means every user has to run `auth.py` again (the server tells them), so mention that in the README's upgrade notes.

## Rate Limits

All Spotify API calls go through `_spotify("method_name", ...)` in `server.py`, which runs the spotipy method off the event loop, retries 429s honouring `Retry-After` (failing fast when the wait is over `MAX_RETRY_WAIT`), and explains failures. Never call spotipy client methods directly. The client uses `_session()` instead of spotipy's own retry settings, which would turn exhausted retries into a fake 429 and sleep out any `Retry-After` themselves. `_session()` retries failed connections, and 5xx on GETs only (a write may already have been applied), then returns the real response; 429s are left to `_spotify()`.

## Page and Batch Limits

From the spec; the constants are at the top of `server.py`:
- `GET /search`: max 10 per request. For filtered queries (`artist:...`) Spotify returns short pages with no `next` link even when later offsets have results, so `_search()` walks offsets until it has enough or a page is empty.
- `GET /artists/{id}/albums`: max 10 per request.
- Other paginated GETs (playlist items, saved tracks, playlists, album tracks): max 50.
- `POST`/`DELETE /playlists/{id}/items`: 100 URIs. `PUT`/`DELETE /me/library`: 40 URIs.

## Search Quirks

Verified against the live API (October 2026):
- Field filters can't be combined with `OR`: `artist:A OR artist:B` returns nothing. Search one artist at a time.
- `artist:` combined with `genre:` returns nothing; `genre:` alone works.

## Deprecated Fields

On `GET /artists/{id}`, the `genres`, `followers`, and `popularity` fields are individually marked deprecated in the OpenAPI spec. Do not expose them in tool output. The same goes for `popularity`, `available_markets` and `linked_from` on tracks and albums. Passing `market` turns on track relinking, which swaps in other track URIs (and only the deprecated `linked_from` says so), so read playlist URIs without a market; `find_playlist_issues` reads `is_playable` in a separate pass and matches it up by position.

Renamed in 2026 (the old names still arrive for now, but are deprecated):
- Playlist objects: `tracks` → `items` (the count is `items.total`; it may be absent for playlists the user doesn't own).
- Playlist entries: `track` → `item`. Use `item` in `fields=` filters, and read it through `_entry_item()`, which falls back to `track`.

## Deprecated Endpoints

Do not use deprecated endpoints. Known deprecated endpoints that must not be used:
- `GET /artists/{id}/related-artists`, `GET /artists/{id}/top-tracks`, `GET /recommendations`, `GET /audio-features`, `GET /audio-analysis/{id}`
- `GET /browse/*` (new releases, categories, featured playlists), `GET /markets`
- Batch lookups such as `GET /tracks?ids=`, `GET /albums?ids=`, `GET /artists?ids=` — fetch items one at a time
- `GET /users/{id}`, `GET /users/{id}/playlists` — use `GET /me` and `GET /me/playlists`
- `POST /users/{user_id}/playlists` — use `current_user_playlist_create` (`POST /me/playlists`)
- `GET`/`POST`/`PUT`/`DELETE /playlists/{id}/tracks` — use the `/playlists/{id}/items` methods
- `PUT`/`DELETE /me/tracks`, `/me/albums`, `/me/episodes`, `/me/shows`, `/me/following`, `/playlists/{id}/followers` and their `/contains` checks — use `PUT`/`DELETE /me/library` and `GET /me/library/contains`, which take URIs

spotipy 2.26 still routes some methods to deprecated endpoints; notably `current_user_unfollow_playlist` (`DELETE /playlists/{id}/followers`). Check the endpoint a spotipy method calls before using it; for `/me/library`, call `_spotify("_put" | "_delete", "me/library", uris=...)`.

## Error Handling

Handle HTTP error codes from the OpenAPI schema and surface meaningful messages. `_explain()` maps Spotify errors to a `SpotifyError` (a `ToolError`), including Premium (`PREMIUM_REQUIRED`), no active device (`NO_ACTIVE_DEVICE`, or a player 404 that mentions the device; other player 404s are a missing track or context), other player restrictions (403), sign-in problems, rate limits and 5xx.

With the MCP SDK, only `ToolError` messages reach the model; any other exception shows up as just "Error executing tool X". Raise `ToolError` for anticipated failures. `SpotifyError.fatal` tells multi-item loops (like queuing) whether to stop or skip the item.

## MCP Server

`server.py` uses the high-level `MCPServer` from the MCP Python SDK 2.x (`mcp>=2.3,<3`), which speaks the 2026-07-28 protocol. Tools are decorated functions whose type hints become the input schema.
- Give every tool a `title` and annotations: `READ_ONLY` for reads, `_changes(destructive=..., idempotent=...)` for writes.
- Read tools return structured output: declare `-> Annotated[CallToolResult, SomeModel]` and return `_result(SomeModel(...))`, which also sends compact JSON text. Write tools return a short `str` with `structured_output=False`.
- Accept URIs, open.spotify.com URLs or bare IDs; normalize with `_id()` / `_uri()`.
- Guidance for the model goes in `INSTRUCTIONS` (sent to every client) or the tool docstrings, not in a client-specific skill.
- Destructive actions can ask the user directly via a `Resolve(...)` resolver that returns `Elicit(...)` (see `delete_playlist`); it must fall back gracefully when the client doesn't support elicitation.
- Don't use MCP logging, sampling or roots; the 2026-07-28 spec deprecates them.

## Logging

The log file is `mcp-spotify.log`, created with owner-only permissions. Keep `spotipy` and `urllib3` at WARNING or above: their DEBUG output contains access tokens, the client secret and full responses. Never log tokens or credentials.

## Caching

Do not cache Spotify content beyond immediate use, per the Spotify Developer Terms.

## Testing

After any change to `server.py`, `spotify_auth.py` or `auth.py`:
1. Run `pytest` and make sure every test passes before considering the task done.
2. For every new tool, add tests in `tests/test_server.py` covering at minimum: normal response, empty/no-result response, and any tool-specific logic (deduplication, pagination, fallbacks).

Tests call the tool functions directly with a mocked spotipy client (the `sp` fixture), and use `mcp.Client(server.mcp)` for behaviour that only exists over the protocol (annotations, elicitation, error results).

## Developer Terms

- Always attribute content to Spotify
- Do not use API data to train ML models
- Do not cache Spotify content beyond immediate use
