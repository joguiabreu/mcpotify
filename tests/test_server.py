import pytest
import spotipy
from unittest.mock import MagicMock, patch, AsyncMock

import server
from server import _handlers

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _track(name="Track A", artist="Artist A", uri="spotify:track:aaa"):
    return {
        "name": name,
        "artists": [{"name": artist}],
        "uri": uri,
    }


def _artist(name="Artist A", uri="spotify:artist:aaa"):
    return {"name": name, "uri": uri, "genres": ["indie"]}


def _album(name="Album A", artist="Artist A", uri="spotify:album:aaa", year="2023"):
    return {
        "name": name,
        "artists": [{"name": artist}],
        "uri": uri,
        "release_date": f"{year}-01-01",
        "album_type": "album",
    }


def _playlist(name="My Playlist", pid="playlist123", total=10, owner="jogui"):
    return {
        "name": name,
        "id": pid,
        "tracks": {"total": total},
        "owner": {"display_name": owner},
    }


def _playback(name="Track A", artist="Artist A", track_uri="spotify:track:aaa",
              artist_uri="spotify:artist:bbb", is_playing=True):
    return {
        "is_playing": is_playing,
        "item": {
            "name": name,
            "artists": [{"name": artist, "uri": artist_uri}],
            "uri": track_uri,
        },
    }


# ---------------------------------------------------------------------------
# search_tracks
# ---------------------------------------------------------------------------

class TestSearchTracks:
    async def test_returns_formatted_tracks(self):
        server.sp.search.return_value = {"tracks": {"items": [_track()]}}
        result = await _handlers["search_tracks"]({"query": "indie"})
        assert "Track A" in result[0].text
        assert "spotify:track:aaa" in result[0].text

    async def test_empty_results(self):
        server.sp.search.return_value = {"tracks": {"items": []}}
        result = await _handlers["search_tracks"]({"query": "xyzxyz"})
        assert result[0].text == "No tracks found."

    async def test_default_limit(self):
        server.sp.search.return_value = {"tracks": {"items": []}}
        await _handlers["search_tracks"]({"query": "test"})
        server.sp.search.assert_called_once_with(q="test", type="track", limit=10)

    async def test_custom_limit(self):
        server.sp.search.return_value = {"tracks": {"items": []}}
        await _handlers["search_tracks"]({"query": "test", "limit": 5})
        server.sp.search.assert_called_once_with(q="test", type="track", limit=5)


# ---------------------------------------------------------------------------
# search_artists
# ---------------------------------------------------------------------------

class TestSearchArtists:
    async def test_returns_formatted_artists(self):
        server.sp.search.return_value = {"artists": {"items": [_artist()]}}
        result = await _handlers["search_artists"]({"query": "Artist A"})
        assert "Artist A" in result[0].text
        assert "spotify:artist:aaa" in result[0].text

    async def test_empty_results(self):
        server.sp.search.return_value = {"artists": {"items": []}}
        result = await _handlers["search_artists"]({"query": "xyzxyz"})
        assert result[0].text == "No artists found."


# ---------------------------------------------------------------------------
# search_albums
# ---------------------------------------------------------------------------

class TestSearchAlbums:
    async def test_returns_formatted_albums(self):
        server.sp.search.return_value = {"albums": {"items": [_album()]}}
        result = await _handlers["search_albums"]({"query": "Album A"})
        assert "Album A" in result[0].text
        assert "2023" in result[0].text
        assert "spotify:album:aaa" in result[0].text

    async def test_empty_results(self):
        server.sp.search.return_value = {"albums": {"items": []}}
        result = await _handlers["search_albums"]({"query": "xyzxyz"})
        assert result[0].text == "No albums found."


# ---------------------------------------------------------------------------
# get_artist_albums
# ---------------------------------------------------------------------------

class TestGetArtistAlbums:
    async def test_returns_albums(self):
        server.sp.artist_albums.return_value = {"items": [_album()]}
        result = await _handlers["get_artist_albums"]({"artist_uri": "spotify:artist:aaa"})
        assert "Album A" in result[0].text
        assert "2023" in result[0].text

    async def test_empty_results(self):
        server.sp.artist_albums.return_value = {"items": []}
        result = await _handlers["get_artist_albums"]({"artist_uri": "spotify:artist:aaa"})
        assert result[0].text == "No albums found for this artist."

    async def test_strips_uri_to_id(self):
        server.sp.artist_albums.return_value = {"items": []}
        await _handlers["get_artist_albums"]({"artist_uri": "spotify:artist:abc123"})
        server.sp.artist_albums.assert_called_once_with("abc123", album_type="album,single", limit=20)

    async def test_default_include_groups(self):
        server.sp.artist_albums.return_value = {"items": []}
        await _handlers["get_artist_albums"]({"artist_uri": "spotify:artist:aaa"})
        call_kwargs = server.sp.artist_albums.call_args
        assert "album,single" in call_kwargs[1]["album_type"]


# ---------------------------------------------------------------------------
# get_album_tracks
# ---------------------------------------------------------------------------

class TestGetAlbumTracks:
    async def test_returns_tracks(self):
        server.sp.album_tracks.return_value = {"items": [_track()], "next": None}
        result = await _handlers["get_album_tracks"]({"album_uri": "spotify:album:aaa"})
        assert "Track A" in result[0].text
        assert "spotify:track:aaa" in result[0].text

    async def test_empty_album(self):
        server.sp.album_tracks.return_value = {"items": [], "next": None}
        result = await _handlers["get_album_tracks"]({"album_uri": "spotify:album:aaa"})
        assert result[0].text == "No tracks found for this album."

    async def test_pagination(self):
        page1 = {"items": [_track("T1", uri="spotify:track:t1")], "next": "page2"}
        page2 = {"items": [_track("T2", uri="spotify:track:t2")], "next": None}
        server.sp.album_tracks.return_value = page1
        server.sp.next.return_value = page2
        result = await _handlers["get_album_tracks"]({"album_uri": "spotify:album:aaa"})
        assert "T1" in result[0].text
        assert "T2" in result[0].text


# ---------------------------------------------------------------------------
# get_current_playing
# ---------------------------------------------------------------------------

class TestGetCurrentPlaying:
    async def test_playing(self):
        server.sp.currently_playing.return_value = _playback(is_playing=True)
        result = await _handlers["get_current_playing"]({})
        assert "Playing" in result[0].text
        assert "Track A" in result[0].text
        assert "Artist A" in result[0].text
        assert "spotify:track:aaa" in result[0].text
        assert "spotify:artist:bbb" in result[0].text

    async def test_paused(self):
        server.sp.currently_playing.return_value = _playback(is_playing=False)
        result = await _handlers["get_current_playing"]({})
        assert "Paused" in result[0].text

    async def test_nothing_playing(self):
        server.sp.currently_playing.return_value = None
        result = await _handlers["get_current_playing"]({})
        assert result[0].text == "Nothing is currently playing."

    async def test_no_item(self):
        server.sp.currently_playing.return_value = {"is_playing": False, "item": None}
        result = await _handlers["get_current_playing"]({})
        assert result[0].text == "Nothing is currently playing."


# ---------------------------------------------------------------------------
# queue_tracks
# ---------------------------------------------------------------------------

class TestQueueTracks:
    async def test_queues_all_tracks(self):
        server.sp.add_to_queue.return_value = None
        result = await _handlers["queue_tracks"]({"track_uris": ["spotify:track:a", "spotify:track:b"]})
        assert server.sp.add_to_queue.call_count == 2
        assert "Queued 2" in result[0].text

    async def test_deduplicates_uris(self):
        server.sp.add_to_queue.return_value = None
        await _handlers["queue_tracks"]({"track_uris": ["spotify:track:a", "spotify:track:a"]})
        assert server.sp.add_to_queue.call_count == 1

    async def test_403_returns_premium_message(self):
        server.sp.add_to_queue.side_effect = spotipy.SpotifyException(403, -1, "Forbidden")
        result = await _handlers["queue_tracks"]({"track_uris": ["spotify:track:a"]})
        assert "Premium" in result[0].text

    async def test_failed_tracks_reported(self):
        err = spotipy.SpotifyException(500, -1, "Error")
        server.sp.add_to_queue.side_effect = [None, err]
        result = await _handlers["queue_tracks"](
            {"track_uris": ["spotify:track:a", "spotify:track:b"]}
        )
        assert "1 failed" in result[0].text


# ---------------------------------------------------------------------------
# queue_recommendations
# ---------------------------------------------------------------------------

class TestQueueRecommendations:
    async def test_queues_with_query(self):
        server.sp.search.return_value = {"tracks": {"items": [_track()]}}
        server.sp.add_to_queue.return_value = None
        result = await _handlers["queue_recommendations"]({"query": "indie", "count": 1})
        assert "Queued 1" in result[0].text

    async def test_falls_back_to_current_track(self):
        server.sp.currently_playing.return_value = _playback()
        server.sp.search.return_value = {"tracks": {"items": [_track()]}}
        server.sp.add_to_queue.return_value = None
        result = await _handlers["queue_recommendations"]({"count": 1})
        assert "Queued 1" in result[0].text

    async def test_nothing_playing_no_query(self):
        server.sp.currently_playing.return_value = None
        result = await _handlers["queue_recommendations"]({})
        assert "No query" in result[0].text

    async def test_no_tracks_found(self):
        server.sp.search.return_value = {"tracks": {"items": []}}
        result = await _handlers["queue_recommendations"]({"query": "xyzxyz"})
        assert "No tracks found" in result[0].text

    async def test_deduplicates_results(self):
        duplicate = _track()
        server.sp.search.return_value = {"tracks": {"items": [duplicate, duplicate]}}
        server.sp.add_to_queue.return_value = None
        await _handlers["queue_recommendations"]({"query": "test", "count": 2})
        assert server.sp.add_to_queue.call_count == 1

    async def test_count_capped_at_50(self):
        server.sp.search.return_value = {"tracks": {"items": []}}
        await _handlers["queue_recommendations"]({"query": "test", "count": 100})
        server.sp.search.assert_called_once_with(q="test", type="track", limit=50)


# ---------------------------------------------------------------------------
# add_to_playlist
# ---------------------------------------------------------------------------

class TestAddToPlaylist:
    def _no_existing(self):
        server.sp.playlist_items.return_value = {"items": [], "next": None}

    async def test_adds_new_tracks(self):
        self._no_existing()
        server.sp.playlist_add_items.return_value = None
        result = await _handlers["add_to_playlist"](
            {"playlist_id": "pl1", "track_uris": ["spotify:track:a", "spotify:track:b"]}
        )
        assert "Added 2" in result[0].text

    async def test_skips_existing_tracks(self):
        server.sp.playlist_items.return_value = {
            "items": [{"track": {"uri": "spotify:track:a"}}], "next": None
        }
        server.sp.playlist_add_items.return_value = None
        result = await _handlers["add_to_playlist"](
            {"playlist_id": "pl1", "track_uris": ["spotify:track:a", "spotify:track:b"]}
        )
        assert "Added 1/2" in result[0].text
        assert "1 already in playlist" in result[0].text

    async def test_all_existing_returns_early(self):
        server.sp.playlist_items.return_value = {
            "items": [{"track": {"uri": "spotify:track:a"}}], "next": None
        }
        result = await _handlers["add_to_playlist"](
            {"playlist_id": "pl1", "track_uris": ["spotify:track:a"]}
        )
        assert "already in the playlist" in result[0].text
        server.sp.playlist_add_items.assert_not_called()

    async def test_paginates_existing_tracks(self):
        page1 = {"items": [{"track": {"uri": "spotify:track:a"}}], "next": "page2"}
        page2 = {"items": [{"track": {"uri": "spotify:track:b"}}], "next": None}
        server.sp.playlist_items.return_value = page1
        server.sp.next.return_value = page2
        server.sp.playlist_add_items.return_value = None
        result = await _handlers["add_to_playlist"](
            {"playlist_id": "pl1", "track_uris": ["spotify:track:a", "spotify:track:b", "spotify:track:c"]}
        )
        assert "Added 1/3" in result[0].text


# ---------------------------------------------------------------------------
# create_playlist
# ---------------------------------------------------------------------------

class TestCreatePlaylist:
    async def test_creates_and_populates(self):
        server.sp.current_user_playlist_create.return_value = {
            "id": "newpl",
            "external_urls": {"spotify": "https://open.spotify.com/playlist/newpl"},
        }
        server.sp.playlist_add_items.return_value = None
        result = await _handlers["create_playlist"](
            {"name": "My Playlist", "track_uris": ["spotify:track:a"]}
        )
        assert "My Playlist" in result[0].text
        assert "https://open.spotify.com" in result[0].text


# ---------------------------------------------------------------------------
# list_my_playlists
# ---------------------------------------------------------------------------

class TestListMyPlaylists:
    async def test_returns_playlists(self):
        server.sp.current_user_playlists.return_value = {"items": [_playlist()]}
        result = await _handlers["list_my_playlists"]({})
        assert "My Playlist" in result[0].text
        assert "playlist123" in result[0].text

    async def test_empty(self):
        server.sp.current_user_playlists.return_value = {"items": []}
        result = await _handlers["list_my_playlists"]({})
        assert result[0].text == "No playlists found."


# ---------------------------------------------------------------------------
# get_my_top_tracks
# ---------------------------------------------------------------------------

class TestGetMyTopTracks:
    async def test_returns_tracks(self):
        server.sp.current_user_top_tracks.return_value = {"items": [_track()]}
        result = await _handlers["get_my_top_tracks"]({})
        assert "Track A" in result[0].text

    async def test_empty(self):
        server.sp.current_user_top_tracks.return_value = {"items": []}
        result = await _handlers["get_my_top_tracks"]({})
        assert result[0].text == "No top tracks found."

    async def test_time_range_label(self):
        server.sp.current_user_top_tracks.return_value = {"items": [_track()]}
        result = await _handlers["get_my_top_tracks"]({"time_range": "short_term"})
        assert "last 4 weeks" in result[0].text


# ---------------------------------------------------------------------------
# get_my_top_artists
# ---------------------------------------------------------------------------

class TestGetMyTopArtists:
    async def test_returns_artists(self):
        server.sp.current_user_top_artists.return_value = {"items": [_artist()]}
        result = await _handlers["get_my_top_artists"]({})
        assert "Artist A" in result[0].text

    async def test_empty(self):
        server.sp.current_user_top_artists.return_value = {"items": []}
        result = await _handlers["get_my_top_artists"]({})
        assert result[0].text == "No top artists found."


# ---------------------------------------------------------------------------
# get_playlist_tracks
# ---------------------------------------------------------------------------

class TestGetPlaylistTracks:
    def _page(self, tracks, next_url=None):
        return {"items": [{"track": t} for t in tracks], "next": next_url}

    async def test_returns_tracks(self):
        server.sp.playlist_items.return_value = self._page([_track()])
        result = await _handlers["get_playlist_tracks"]({"playlist_id": "pl1"})
        assert "Track A" in result[0].text
        assert "spotify:track:aaa" in result[0].text

    async def test_empty_playlist(self):
        server.sp.playlist_items.return_value = self._page([])
        result = await _handlers["get_playlist_tracks"]({"playlist_id": "pl1"})
        assert result[0].text == "No tracks found in this playlist."

    async def test_skips_local_tracks(self):
        local = {"name": "Local", "artists": [{"name": "Me"}], "uri": "spotify:local:x"}
        server.sp.playlist_items.return_value = self._page([local])
        result = await _handlers["get_playlist_tracks"]({"playlist_id": "pl1"})
        assert result[0].text == "No tracks found in this playlist."

    async def test_skips_null_items(self):
        page = {"items": [None, {"track": None}, {"track": _track()}], "next": None}
        server.sp.playlist_items.return_value = page
        result = await _handlers["get_playlist_tracks"]({"playlist_id": "pl1"})
        assert "Track A" in result[0].text

    async def test_pagination(self):
        page1 = {"items": [{"track": _track("T1", uri="spotify:track:t1")}], "next": "page2"}
        page2 = {"items": [{"track": _track("T2", uri="spotify:track:t2")}], "next": None}
        server.sp.playlist_items.return_value = page1
        server.sp.next.return_value = page2
        result = await _handlers["get_playlist_tracks"]({"playlist_id": "pl1"})
        assert "T1" in result[0].text
        assert "T2" in result[0].text


# ---------------------------------------------------------------------------
# remove_from_playlist
# ---------------------------------------------------------------------------

class TestRemoveFromPlaylist:
    async def test_removes_tracks(self):
        server.sp.playlist_remove_all_occurrences_of_items.return_value = None
        result = await _handlers["remove_from_playlist"](
            {"playlist_id": "pl1", "track_uris": ["spotify:track:a", "spotify:track:b"]}
        )
        server.sp.playlist_remove_all_occurrences_of_items.assert_called_once_with(
            "pl1", ["spotify:track:a", "spotify:track:b"]
        )
        assert "Removed 2" in result[0].text

    async def test_single_track(self):
        server.sp.playlist_remove_all_occurrences_of_items.return_value = None
        result = await _handlers["remove_from_playlist"](
            {"playlist_id": "pl1", "track_uris": ["spotify:track:a"]}
        )
        assert "Removed 1" in result[0].text


# ---------------------------------------------------------------------------
# delete_playlist
# ---------------------------------------------------------------------------

class TestDeletePlaylist:
    async def test_deletes_playlist(self):
        server.sp.current_user_unfollow_playlist.return_value = None
        result = await _handlers["delete_playlist"]({"playlist_id": "pl1"})
        server.sp.current_user_unfollow_playlist.assert_called_once_with("pl1")
        assert "removed" in result[0].text

# ---------------------------------------------------------------------------
# control_playback
# ---------------------------------------------------------------------------

class TestControlPlayback:
    async def test_play(self):
        server.sp.start_playback.return_value = None
        result = await _handlers["control_playback"]({"action": "play"})
        server.sp.start_playback.assert_called_once()
        assert "Resumed" in result[0].text

    async def test_pause(self):
        server.sp.pause_playback.return_value = None
        result = await _handlers["control_playback"]({"action": "pause"})
        server.sp.pause_playback.assert_called_once()
        assert "Paused" in result[0].text

    async def test_skip_next(self):
        server.sp.next_track.return_value = None
        result = await _handlers["control_playback"]({"action": "skip_next"})
        server.sp.next_track.assert_called_once()
        assert "next track" in result[0].text

    async def test_skip_previous(self):
        server.sp.previous_track.return_value = None
        result = await _handlers["control_playback"]({"action": "skip_previous"})
        server.sp.previous_track.assert_called_once()
        assert "previous track" in result[0].text

    async def test_403_returns_premium_message(self):
        server.sp.start_playback.side_effect = spotipy.SpotifyException(403, -1, "Forbidden")
        result = await _handlers["control_playback"]({"action": "play"})
        assert "Premium" in result[0].text


# ---------------------------------------------------------------------------
# _spotify retry logic
# ---------------------------------------------------------------------------

class TestSpotifyRetry:
    async def test_retries_on_429(self):
        mock_fn = MagicMock()
        rate_limit_err = spotipy.SpotifyException(429, -1, "Too Many Requests")
        rate_limit_err.headers = {"Retry-After": "0"}
        mock_fn.side_effect = [rate_limit_err, "success"]

        with patch("server.asyncio.sleep") as mock_sleep:
            result = await server._spotify(mock_fn)

        assert result == "success"
        assert mock_fn.call_count == 2
        mock_sleep.assert_called_once()

    async def test_does_not_retry_on_other_errors(self):
        mock_fn = MagicMock(side_effect=spotipy.SpotifyException(404, -1, "Not Found"))
        with pytest.raises(spotipy.SpotifyException):
            await server._spotify(mock_fn)
        assert mock_fn.call_count == 1

    async def test_raises_after_max_retries(self):
        rate_limit_err = spotipy.SpotifyException(429, -1, "Too Many Requests")
        rate_limit_err.headers = {"Retry-After": "0"}
        mock_fn = MagicMock(side_effect=rate_limit_err)

        with patch("server.asyncio.sleep"), pytest.raises(spotipy.SpotifyException):
            await server._spotify(mock_fn)

        assert mock_fn.call_count == 5  # max_retries
