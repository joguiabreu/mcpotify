import asyncio
import json
import logging
import os
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import requests
import spotipy
from mcp import Client
from mcp.server.mcpserver import AcceptedElicitation, DeclinedElicitation, Elicit
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ElicitResult
from spotipy.oauth2 import SpotifyOauthError

import server
import spotify_auth
from server import SpotifyError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _track(name="Track A", artist="Artist A", uri="spotify:track:aaa", album=None):
    track = {"name": name, "artists": [{"name": artist, "uri": "spotify:artist:art"}], "uri": uri}
    if album:
        track["album"] = {"name": album}
    return track


def _episode(name="Episode A", uri="spotify:episode:eee", show="Show A"):
    return {"type": "episode", "name": name, "uri": uri, "show": {"name": show},
            "duration_ms": 1_800_000, "release_date": "2026-01-01"}


def _artist(name="Artist A", uri="spotify:artist:aaa"):
    return {"name": name, "uri": uri, "genres": ["indie"], "followers": {"total": 5}, "popularity": 50}


def _album(name="Album A", artist="Artist A", uri="spotify:album:aaa", year="2023"):
    return {"name": name, "artists": [{"name": artist}], "uri": uri,
            "release_date": f"{year}-01-01", "album_type": "album", "total_tracks": 10}


def _playlist(name="My Playlist", pid="playlist123", total=10, owner="jogui", owner_id="me", field="items"):
    return {"name": name, "id": pid, field: {"total": total},
            "owner": {"display_name": owner, "id": owner_id}, "public": False}


def _page(items, next_url=None, total=None):
    return {"items": items, "next": next_url, "total": total if total is not None else len(items)}


def _entries(*tracks, key="item"):
    return [{key: t} for t in tracks]


def _playback(name="Track A", artist="Artist A", track_uri="spotify:track:aaa",
              artist_uri="spotify:artist:bbb", is_playing=True):
    return {
        "is_playing": is_playing,
        "progress_ms": 61_000,
        "shuffle_state": False,
        "repeat_state": "off",
        "context": {"uri": "spotify:playlist:ctx"},
        "device": {"id": "dev1", "name": "Kitchen", "type": "Speaker", "is_active": True, "volume_percent": 40},
        "item": {"name": name, "artists": [{"name": artist, "uri": artist_uri}], "uri": track_uri},
    }


def _error(status, reason=None, message="Error", headers=None):
    return spotipy.SpotifyException(status, -1, f"https://api.spotify.com/v1/x:\n {message}",
                                     reason=reason, headers=headers)


def data(result):
    return result.structured_content


def ctx_with(elicitation=None):
    ctx = MagicMock()
    ctx.client_capabilities = MagicMock(elicitation=elicitation) if elicitation is not False else None
    return ctx


# ---------------------------------------------------------------------------
# search_tracks
# ---------------------------------------------------------------------------

class TestSearchTracks:
    async def test_returns_tracks(self, sp):
        sp.search.return_value = {"tracks": _page([_track(album="Album A")])}
        result = await server.search_tracks("indie")
        assert data(result) == {"tracks": [
            {"name": "Track A", "artists": ["Artist A"], "uri": "spotify:track:aaa", "album": "Album A"}
        ]}
        assert json.loads(result.content[0].text) == data(result)

    async def test_empty_results(self, sp):
        sp.search.return_value = {"tracks": _page([])}
        result = await server.search_tracks("xyzxyz")
        assert data(result) == {"tracks": []}

    async def test_default_limit(self, sp):
        sp.search.return_value = {"tracks": _page([])}
        await server.search_tracks("test")
        sp.search.assert_called_once_with(q="test", type="track", limit=10, offset=0)

    async def test_pages_past_spotify_cap_of_10(self, sp):
        first = [_track(f"T{i}", uri=f"spotify:track:t{i}") for i in range(10)]
        second = [_track(f"T{i}", uri=f"spotify:track:t{i}") for i in range(10, 15)]
        sp.search.side_effect = [{"tracks": _page(first, "next")}, {"tracks": _page(second)}]
        result = await server.search_tracks("test", limit=15)
        assert len(data(result)["tracks"]) == 15
        assert sp.search.call_args_list == [
            call(q="test", type="track", limit=10, offset=0),
            call(q="test", type="track", limit=10, offset=10),
        ]

    async def test_keeps_paging_past_short_pages(self, sp):
        # Filtered searches return short pages without `next`, yet later offsets have more.
        first = [_track(uri=f"spotify:track:t{i}") for i in range(5)]
        second = [_track(uri=f"spotify:track:u{i}") for i in range(6)]
        sp.search.side_effect = [{"tracks": _page(first)}, {"tracks": _page(second)}]
        result = await server.search_tracks("artist:Khruangbin", limit=10)
        assert len(data(result)["tracks"]) == 10
        assert sp.search.call_count == 2

    async def test_stops_at_empty_page(self, sp):
        sp.search.side_effect = [{"tracks": _page([_track()])}, {"tracks": _page([])}]
        result = await server.search_tracks("test", limit=50)
        assert len(data(result)["tracks"]) == 1
        assert sp.search.call_count == 2

    async def test_skips_nulls_and_duplicates(self, sp):
        sp.search.return_value = {"tracks": _page([_track(), None, _track()])}
        result = await server.search_tracks("test")
        assert len(data(result)["tracks"]) == 1


# ---------------------------------------------------------------------------
# search_artists / search_albums / search_podcasts
# ---------------------------------------------------------------------------

class TestSearchArtists:
    async def test_returns_artists_without_deprecated_fields(self, sp):
        sp.search.return_value = {"artists": _page([_artist()])}
        result = await server.search_artists("Artist A")
        assert data(result) == {"artists": [{"name": "Artist A", "uri": "spotify:artist:aaa"}]}

    async def test_empty_results(self, sp):
        sp.search.return_value = {"artists": _page([])}
        result = await server.search_artists("xyzxyz")
        assert data(result) == {"artists": []}


class TestSearchAlbums:
    async def test_returns_albums(self, sp):
        sp.search.return_value = {"albums": _page([_album()])}
        result = await server.search_albums("Album A")
        album = data(result)["albums"][0]
        assert album["year"] == "2023"
        assert album["uri"] == "spotify:album:aaa"

    async def test_empty_results(self, sp):
        sp.search.return_value = {"albums": _page([])}
        result = await server.search_albums("xyzxyz")
        assert data(result) == {"albums": []}


class TestSearchPodcasts:
    async def test_shows(self, sp):
        show = {"name": "Show A", "uri": "spotify:show:sss", "description": "x" * 300, "total_episodes": 12}
        sp.search.return_value = {"shows": _page([show])}
        result = await server.search_podcasts("history")
        found = data(result)["shows"][0]
        assert found["name"] == "Show A"
        assert len(found["description"]) == 201  # truncated with an ellipsis
        assert sp.search.call_args_list[0] == call(q="history", type="show", limit=10, offset=0)

    async def test_episodes(self, sp):
        sp.search.return_value = {"episodes": _page([_episode()])}
        result = await server.search_podcasts("history", kind="episode")
        assert data(result)["episodes"][0]["duration_minutes"] == 30

    async def test_empty_results(self, sp):
        sp.search.return_value = {"shows": _page([])}
        result = await server.search_podcasts("xyzxyz")
        assert data(result) == {"shows": []}


# ---------------------------------------------------------------------------
# get_artist_albums / get_album_tracks / get_show_episodes
# ---------------------------------------------------------------------------

class TestGetArtistAlbums:
    async def test_returns_albums(self, sp):
        sp.artist_albums.return_value = _page([_album()])
        result = await server.get_artist_albums("spotify:artist:aaa")
        assert data(result)["albums"][0]["name"] == "Album A"

    async def test_empty_results(self, sp):
        sp.artist_albums.return_value = _page([])
        result = await server.get_artist_albums("spotify:artist:aaa")
        assert data(result) == {"albums": []}

    async def test_uses_include_groups_and_pages_of_10(self, sp):
        sp.artist_albums.side_effect = [
            _page([_album(uri=f"spotify:album:a{i}") for i in range(10)], "next"),
            _page([_album(uri="spotify:album:last")]),
        ]
        result = await server.get_artist_albums("https://open.spotify.com/artist/abc123?si=x", limit=20)
        assert len(data(result)["albums"]) == 11
        assert sp.artist_albums.call_args_list == [
            call("abc123", include_groups="album,single", limit=10, offset=0),
            call("abc123", include_groups="album,single", limit=10, offset=10),
        ]

    async def test_rejects_wrong_uri_type(self, sp):
        with pytest.raises(ToolError, match="Expected a Spotify artist"):
            await server.get_artist_albums("spotify:album:abc")


class TestGetAlbumTracks:
    async def test_returns_tracks(self, sp):
        sp.album_tracks.return_value = _page([_track()])
        result = await server.get_album_tracks("spotify:album:aaa")
        assert data(result)["tracks"][0]["uri"] == "spotify:track:aaa"

    async def test_empty_album(self, sp):
        sp.album_tracks.return_value = _page([])
        result = await server.get_album_tracks("spotify:album:aaa")
        assert data(result) == {"tracks": []}

    async def test_pagination(self, sp):
        sp.album_tracks.side_effect = [
            _page([_track(f"T{i}", uri=f"spotify:track:t{i}") for i in range(50)], "page2"),
            _page([_track("Last", uri="spotify:track:last")]),
        ]
        result = await server.get_album_tracks("spotify:album:aaa")
        assert len(data(result)["tracks"]) == 51
        assert sp.album_tracks.call_args_list[1] == call("aaa", limit=50, offset=50)


class TestGetShowEpisodes:
    async def test_returns_episodes_with_resume_point(self, sp):
        episode = _episode() | {"resume_point": {"resume_position_ms": 600_000, "fully_played": False}}
        sp.show_episodes.return_value = _page([episode], "next", total=40)
        result = await server.get_show_episodes("spotify:show:sss", limit=1)
        assert data(result)["episodes"][0]["resume_minutes"] == 10
        assert data(result)["next_offset"] == 1
        assert data(result)["total"] == 40

    async def test_empty(self, sp):
        sp.show_episodes.return_value = _page([])
        result = await server.get_show_episodes("spotify:show:sss")
        assert data(result) == {"episodes": [], "total": 0}


# ---------------------------------------------------------------------------
# get_my_top_tracks / get_my_top_artists / get_recently_played
# ---------------------------------------------------------------------------

class TestGetMyTopTracks:
    async def test_returns_tracks(self, sp):
        sp.current_user_top_tracks.return_value = _page([_track()])
        result = await server.get_my_top_tracks()
        assert data(result)["tracks"][0]["name"] == "Track A"
        assert data(result)["period"] == "last 6 months"

    async def test_empty(self, sp):
        sp.current_user_top_tracks.return_value = _page([])
        result = await server.get_my_top_tracks()
        assert data(result)["tracks"] == []

    async def test_time_range_label(self, sp):
        sp.current_user_top_tracks.return_value = _page([_track()])
        result = await server.get_my_top_tracks(time_range="short_term")
        assert data(result)["period"] == "last 4 weeks"


class TestGetMyTopArtists:
    async def test_returns_artists(self, sp):
        sp.current_user_top_artists.return_value = _page([_artist()])
        result = await server.get_my_top_artists()
        assert data(result)["artists"] == [{"name": "Artist A", "uri": "spotify:artist:aaa"}]

    async def test_empty(self, sp):
        sp.current_user_top_artists.return_value = _page([])
        result = await server.get_my_top_artists()
        assert data(result)["artists"] == []


class TestGetRecentlyPlayed:
    async def test_returns_tracks_with_play_time(self, sp):
        sp.current_user_recently_played.return_value = _page([{"track": _track(), "played_at": "2026-10-15T09:00:00Z"}])
        result = await server.get_recently_played(limit=5)
        assert data(result)["tracks"][0]["played_at"] == "2026-10-15T09:00:00Z"
        sp.current_user_recently_played.assert_called_once_with(limit=5)

    async def test_empty(self, sp):
        sp.current_user_recently_played.return_value = _page([])
        result = await server.get_recently_played()
        assert data(result) == {"tracks": []}


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

class TestGetLikedSongs:
    async def test_returns_tracks_and_paging(self, sp):
        sp.current_user_saved_tracks.return_value = _page(_entries(_track(), key="track"), "next", total=300)
        result = await server.get_liked_songs(limit=1)
        assert data(result)["tracks"][0]["name"] == "Track A"
        assert data(result)["total"] == 300
        assert data(result)["next_offset"] == 1

    async def test_empty(self, sp):
        sp.current_user_saved_tracks.return_value = _page([])
        result = await server.get_liked_songs()
        assert data(result)["tracks"] == []


class TestSaveToLibrary:
    async def test_saves_with_library_endpoint(self, sp):
        result = await server.save_to_library(["spotify:track:a", "spotify:album:b", "spotify:track:a"])
        sp._put.assert_called_once_with("me/library", uris="spotify:track:a,spotify:album:b")
        assert "Saved 2" in result

    async def test_batches_of_40(self, sp):
        await server.save_to_library([f"spotify:track:t{i}" for i in range(45)])
        assert sp._put.call_count == 2

    async def test_bare_id_is_a_track(self, sp):
        await server.save_to_library(["abc123"])
        sp._put.assert_called_once_with("me/library", uris="spotify:track:abc123")

    async def test_rejects_playlists(self, sp):
        with pytest.raises(ToolError):
            await server.save_to_library(["spotify:playlist:p1"])
        sp._put.assert_not_called()


class TestRemoveFromLibrary:
    async def test_removes_with_library_endpoint(self, sp):
        result = await server.remove_from_library(["spotify:track:a"])
        sp._delete.assert_called_once_with("me/library", uris="spotify:track:a")
        assert "Removed 1" in result

    async def test_rejects_artists(self, sp):
        with pytest.raises(ToolError):
            await server.remove_from_library(["spotify:artist:a"])


# ---------------------------------------------------------------------------
# list_my_playlists / get_playlist_tracks
# ---------------------------------------------------------------------------

class TestListMyPlaylists:
    async def test_returns_playlists(self, sp):
        sp.current_user_playlists.return_value = _page([_playlist()])
        result = await server.list_my_playlists()
        assert data(result)["playlists"] == [
            {"name": "My Playlist", "id": "playlist123", "owner": "jogui", "tracks": 10, "public": False}
        ]

    async def test_empty(self, sp):
        sp.current_user_playlists.return_value = _page([])
        result = await server.list_my_playlists()
        assert data(result)["playlists"] == []

    async def test_reads_legacy_tracks_field(self, sp):
        sp.current_user_playlists.return_value = _page([_playlist(field="tracks", total=7)])
        result = await server.list_my_playlists()
        assert data(result)["playlists"][0]["tracks"] == 7

    async def test_count_missing_for_playlists_not_owned(self, sp):
        playlist = _playlist()
        del playlist["items"]
        sp.current_user_playlists.return_value = _page([playlist])
        result = await server.list_my_playlists()
        assert "tracks" not in data(result)["playlists"][0]


class TestGetPlaylistTracks:
    async def test_returns_tracks_with_positions(self, sp):
        sp.playlist_items.return_value = _page(_entries(_track(), _track("B", uri="spotify:track:b")))
        result = await server.get_playlist_tracks("pl1")
        assert [(t["name"], t["position"]) for t in data(result)["tracks"]] == [("Track A", 0), ("B", 1)]

    async def test_empty_playlist(self, sp):
        sp.playlist_items.return_value = _page([])
        result = await server.get_playlist_tracks("pl1")
        assert data(result)["tracks"] == []

    async def test_uses_new_item_field_names(self, sp):
        sp.playlist_items.return_value = _page([])
        await server.get_playlist_tracks("spotify:playlist:pl1")
        assert sp.playlist_items.call_args.kwargs["fields"].startswith("items(is_local,item(")

    async def test_reads_legacy_track_key(self, sp):
        sp.playlist_items.return_value = _page(_entries(_track(), key="track"))
        result = await server.get_playlist_tracks("pl1")
        assert data(result)["tracks"][0]["name"] == "Track A"

    async def test_skips_local_files_and_nulls_keeping_positions(self, sp):
        local = {"name": "Local", "artists": [{"name": "Me"}], "uri": "spotify:local:x"}
        sp.playlist_items.return_value = _page([None, {"item": None}, {"item": local, "is_local": True}, {"item": _track()}])
        result = await server.get_playlist_tracks("pl1")
        assert data(result)["tracks"][0]["position"] == 3
        assert data(result)["skipped_local_files"] == 1

    async def test_episodes_show_their_show(self, sp):
        sp.playlist_items.return_value = _page(_entries(_episode()))
        result = await server.get_playlist_tracks("pl1")
        assert data(result)["tracks"][0]["artists"] == ["Show A"]

    async def test_pages_of_50_with_offset(self, sp):
        sp.playlist_items.side_effect = [
            _page(_entries(*[_track(uri=f"spotify:track:t{i}") for i in range(50)]), "next", total=300),
            _page(_entries(*[_track(uri=f"spotify:track:u{i}") for i in range(10)]), "next", total=300),
        ]
        result = await server.get_playlist_tracks("pl1", limit=60, offset=100)
        assert [c.kwargs["offset"] for c in sp.playlist_items.call_args_list] == [100, 150]
        assert data(result)["tracks"][-1]["position"] == 159
        assert data(result)["next_offset"] == 160
        assert data(result)["total"] == 300


# ---------------------------------------------------------------------------
# create_playlist / add_to_playlist / remove_from_playlist
# ---------------------------------------------------------------------------

class TestCreatePlaylist:
    def _created(self, sp):
        sp.current_user_playlist_create.return_value = {
            "id": "newpl", "external_urls": {"spotify": "https://open.spotify.com/playlist/newpl"},
        }

    async def test_creates_private_playlist_and_populates(self, sp):
        self._created(sp)
        result = await server.create_playlist("My Playlist", ["spotify:track:a"])
        sp.current_user_playlist_create.assert_called_once_with(name="My Playlist", public=False, description="")
        sp.playlist_add_items.assert_called_once_with("newpl", ["spotify:track:a"])
        assert "private playlist 'My Playlist'" in result
        assert "ID: newpl" in result
        assert "https://open.spotify.com" in result

    async def test_empty_playlist(self, sp):
        self._created(sp)
        result = await server.create_playlist("Empty")
        sp.playlist_add_items.assert_not_called()
        assert "0 track(s)" in result

    async def test_batches_of_100(self, sp):
        self._created(sp)
        await server.create_playlist("Big", [f"spotify:track:t{i}" for i in range(150)], public=True)
        assert [len(c.args[1]) for c in sp.playlist_add_items.call_args_list] == [100, 50]

    async def test_reports_partial_failure(self, sp):
        self._created(sp)
        sp.playlist_add_items.side_effect = [None, _error(500)]
        with pytest.raises(SpotifyError, match="ID: newpl.*stopped after 100 of 150"):
            await server.create_playlist("Big", [f"spotify:track:t{i}" for i in range(150)])


class TestAddToPlaylist:
    async def test_adds_new_tracks(self, sp):
        sp.playlist_items.return_value = _page([])
        result = await server.add_to_playlist("pl1", ["spotify:track:a", "spotify:track:b"])
        sp.playlist_add_items.assert_called_once_with("pl1", ["spotify:track:a", "spotify:track:b"])
        assert "Added 2" in result

    async def test_skips_existing_tracks(self, sp):
        sp.playlist_items.return_value = _page(_entries({"uri": "spotify:track:a"}))
        result = await server.add_to_playlist("pl1", ["spotify:track:a", "spotify:track:b"])
        assert "Added 1/2" in result
        assert "1 already in playlist" in result

    async def test_all_existing_returns_early(self, sp):
        sp.playlist_items.return_value = _page(_entries({"uri": "spotify:track:a"}, key="track"))
        result = await server.add_to_playlist("pl1", ["spotify:track:a"])
        assert "already in the playlist" in result
        sp.playlist_add_items.assert_not_called()

    async def test_paginates_existing_tracks(self, sp):
        sp.playlist_items.side_effect = [
            _page(_entries(*[{"uri": f"spotify:track:x{i}"} for i in range(49)], {"uri": "spotify:track:a"}), "page2"),
            _page(_entries({"uri": "spotify:track:b"})),
        ]
        result = await server.add_to_playlist("pl1", ["spotify:track:a", "spotify:track:b", "spotify:track:c"])
        assert "Added 1/3" in result


class TestRemoveFromPlaylist:
    async def test_removes_only_tracks_in_the_playlist(self, sp):
        sp.playlist_items.return_value = _page(_entries({"uri": "spotify:track:a"}))
        result = await server.remove_from_playlist("pl1", ["spotify:track:a", "spotify:track:b"])
        sp.playlist_remove_all_occurrences_of_items.assert_called_once_with("pl1", ["spotify:track:a"])
        assert "Removed 1 track(s)." in result
        assert "1 weren't in the playlist: spotify:track:b" in result

    async def test_nothing_to_remove(self, sp):
        sp.playlist_items.return_value = _page([])
        result = await server.remove_from_playlist("pl1", ["spotify:track:a"])
        sp.playlist_remove_all_occurrences_of_items.assert_not_called()
        assert "nothing removed" in result

    async def test_reports_copies(self, sp):
        sp.playlist_items.return_value = _page(_entries({"uri": "spotify:track:a"}, {"uri": "spotify:track:a"}))
        result = await server.remove_from_playlist("pl1", ["spotify:track:a"])
        assert "(2 copies in total)" in result


# ---------------------------------------------------------------------------
# update_playlist_details / reorder_playlist / delete_playlist
# ---------------------------------------------------------------------------

class TestUpdatePlaylistDetails:
    async def test_renames(self, sp):
        result = await server.update_playlist_details("pl1", name="New name")
        sp.playlist_change_details.assert_called_once_with("pl1", name="New name", public=None, description=None)
        assert "name to 'New name'" in result

    async def test_nothing_to_change(self, sp):
        with pytest.raises(ToolError, match="Nothing to change"):
            await server.update_playlist_details("pl1")
        sp.playlist_change_details.assert_not_called()


class TestReorderPlaylist:
    async def test_moves_items(self, sp):
        result = await server.reorder_playlist("pl1", range_start=5, insert_before=0)
        sp.playlist_reorder_items.assert_called_once_with("pl1", range_start=5, insert_before=0, range_length=1)
        assert "from position 5 to before position 0" in result

    async def test_rejects_wrong_id_type(self, sp):
        with pytest.raises(ToolError):
            await server.reorder_playlist("spotify:track:x", range_start=1, insert_before=0)


class TestDeletePlaylist:
    async def test_deletes_when_confirmed(self, sp):
        confirmed = AcceptedElicitation(data=server.DeleteConfirmation(confirm=True))
        result = await server.delete_playlist("pl1", confirmed)
        sp._delete.assert_called_once_with("me/library", uris="spotify:playlist:pl1")
        sp.current_user_unfollow_playlist.assert_not_called()  # deprecated endpoint
        assert "removed" in result

    async def test_keeps_playlist_when_declined(self, sp):
        result = await server.delete_playlist("pl1", DeclinedElicitation())
        sp._delete.assert_not_called()
        assert "kept" in result

    async def test_keeps_playlist_when_unchecked(self, sp):
        unchecked = AcceptedElicitation(data=server.DeleteConfirmation(confirm=False))
        await server.delete_playlist("pl1", unchecked)
        sp._delete.assert_not_called()

    async def test_resolver_asks_with_playlist_name(self, sp):
        sp.playlist.return_value = {"name": "Road Trip"}
        ask = await server._confirm_delete("pl1", ctx_with(elicitation=MagicMock(form=object(), url=None)))
        assert isinstance(ask, Elicit)
        assert "Road Trip" in ask.message

    async def test_resolver_skips_question_without_elicitation_support(self, sp):
        answer = await server._confirm_delete("pl1", ctx_with(elicitation=False))
        assert answer.confirm is True
        sp.playlist.assert_not_called()


# ---------------------------------------------------------------------------
# find_playlist_issues / remove_playlist_duplicates
# ---------------------------------------------------------------------------

class TestBaseTitle:
    @pytest.mark.parametrize("name", ["Song", "Song - Remastered 2011", "Song (Live)", "Song (feat. X) - Radio Edit", "  SONG "])
    def test_strips_version_suffixes(self, name):
        assert server._base_title(name) == "song"

    def test_keeps_inner_dashes(self):
        assert server._base_title("Run-DMC Song") == "run-dmc song"


class TestFindPlaylistIssues:
    async def test_reports_issues_in_owned_playlists_only(self, sp):
        ctx = AsyncMock()
        sp.current_user.return_value = {"id": "me"}
        sp.current_user_playlists.return_value = _page([
            _playlist("Mine", "p1"), _playlist("Theirs", "p2", owner_id="other"), _playlist("Clean", "p3"),
        ])
        problem_entries = [
            {"item": _track("Song", uri="spotify:track:a")},
            {"item": _track("Song - Remastered", uri="spotify:track:b")},
            {"item": _track("Song", uri="spotify:track:a")},
            {"item": _track("Gone", uri="spotify:track:g")},
            {"item": {"name": "Demo", "uri": "spotify:local:demo"}, "is_local": True},
        ]
        playable = [True, True, True, False, None]

        def playlist_items(pid, fields, limit, offset, market=None):
            if market:
                return _page([{"item": {"is_playable": p}} for p in playable])
            return _page(problem_entries if pid == "p1" else _entries(_track()))

        sp.playlist_items.side_effect = playlist_items
        result = await server.find_playlist_issues(ctx)
        audit = data(result)
        assert audit["playlists_checked"] == 2
        [issues] = audit["playlists_with_issues"]
        assert issues["name"] == "Mine"
        assert issues["duplicates"][0]["positions"] == [0, 2]
        assert [v["uri"] for v in issues["likely_duplicates"][0]["versions"]] == ["spotify:track:a", "spotify:track:b"]
        assert issues["unavailable"] == [
            {"name": "Gone", "artists": ["Artist A"], "uri": "spotify:track:g", "position": 3}
        ]
        assert issues["local_files"] == 1
        assert ctx.report_progress.await_count == 2

    def _audit_three(self, sp, failure):
        """Every owned playlist is p1 (a duplicate), p2 (fails with `failure`) and p3 (another duplicate)."""
        sp.current_user.return_value = {"id": "me"}
        sp.current_user_playlists.return_value = _page([_playlist("One", "p1"), _playlist("Two", "p2"), _playlist("Three", "p3")])

        def playlist_items(pid, fields, limit, offset, market=None):
            if pid == "p2":
                raise failure
            return _page(_entries(_track(), _track()))

        sp.playlist_items.side_effect = playlist_items

    async def test_rate_limit_partway_keeps_what_was_checked(self, sp):
        self._audit_three(sp, _error(429, headers={"Retry-After": "3600"}))
        result = await server.find_playlist_issues(AsyncMock())
        audit = data(result)
        assert audit["playlists_checked"] == 1
        assert [p["name"] for p in audit["playlists_with_issues"]] == ["One"]
        assert audit["stopped_early"].startswith("Stopped at playlist 2 of 3 (Two): Spotify is rate limiting")
        assert not result.is_error

    async def test_failure_on_the_first_playlist_is_an_error(self, sp):
        sp.playlist.return_value = {"name": "Mine"}
        sp.playlist_items.side_effect = _error(429, headers={"Retry-After": "3600"})
        with pytest.raises(SpotifyError, match="rate limiting"):
            await server.find_playlist_issues(AsyncMock(), playlist_ids=["p1", "p2"])

    async def test_skips_playlists_it_cannot_read(self, sp):
        self._audit_three(sp, _error(403, message="Forbidden"))
        audit = data(await server.find_playlist_issues(AsyncMock()))
        assert audit["playlists_checked"] == 2
        assert [p["name"] for p in audit["playlists_with_issues"]] == ["One", "Three"]
        assert audit["skipped"] == ["Two: Spotify only shares the items of playlists the user owns or collaborates on."]
        assert "stopped_early" not in audit

    async def test_failure_after_skipped_playlists_keeps_them(self, sp):
        sp.playlist.side_effect = [_error(404, message="Invalid playlist Id"), {"name": "Mine"}]
        sp.playlist_items.side_effect = _error(429, headers={"Retry-After": "3600"})
        result = await server.find_playlist_issues(AsyncMock(), playlist_ids=["bad", "p1"])
        audit = data(result)
        assert audit["playlists_checked"] == 0
        assert audit["skipped"] == ["bad: Not found on Spotify: Invalid playlist Id"]
        assert audit["stopped_early"].startswith("Stopped at playlist 2 of 2 (p1): Spotify is rate limiting")
        assert not result.is_error

    async def test_same_playlist_in_different_forms_is_checked_once(self, sp):
        sp.playlist.return_value = {"name": "Mine"}
        sp.playlist_items.return_value = _page(_entries(_track(), _track()))
        audit = data(await server.find_playlist_issues(
            AsyncMock(), playlist_ids=["https://open.spotify.com/playlist/p1?si=x", "spotify:playlist:p1", "p1"],
        ))
        assert audit["playlists_checked"] == 1
        assert len(audit["playlists_with_issues"]) == 1
        sp.playlist.assert_called_once()

    async def test_skips_unknown_playlist_ids(self, sp):
        sp.playlist.side_effect = [_error(404, message="Invalid playlist Id"), {"name": "Mine"}]
        sp.playlist_items.return_value = _page(_entries(_track()))
        audit = data(await server.find_playlist_issues(AsyncMock(), playlist_ids=["bad", "p1"]))
        assert audit["playlists_checked"] == 1
        assert audit["skipped"] == ["bad: Not found on Spotify: Invalid playlist Id"]

    async def test_reports_uris_without_relinking(self, sp):
        # With a market, Spotify relinks A to B; then A would look like a second copy of B.
        def playlist_items(pid, fields, limit, offset, market=None):
            if market:
                return _page(_entries({"uri": "spotify:track:b", "is_playable": True}, {"uri": "spotify:track:b"}))
            return _page(_entries(_track(uri="spotify:track:a"), _track("Other", uri="spotify:track:b")))

        sp.playlist.return_value = {"name": "Mine"}
        sp.playlist_items.side_effect = playlist_items
        result = await server.find_playlist_issues(AsyncMock(), playlist_ids=["p1"])
        assert data(result)["playlists_with_issues"] == []
        names_read = [c for c in sp.playlist_items.call_args_list if "name" in c.kwargs["fields"]]
        assert all("market" not in c.kwargs for c in names_read)

    async def test_nothing_found(self, sp):
        sp.playlist.return_value = {"name": "Clean"}
        sp.playlist_items.return_value = _page(_entries(_track()))
        result = await server.find_playlist_issues(AsyncMock(), playlist_ids=["spotify:playlist:p1"])
        assert data(result) == {"playlists_checked": 1, "playlists_with_issues": []}
        sp.current_user_playlists.assert_not_called()


def _uri_entries(*ids):
    """Playlist entries for track IDs; None stands for a track Spotify removed."""
    return _page([{"item": {"uri": f"spotify:track:{i}"} if i else None} for i in ids])


class FakePlaylist:
    """Applies the playlist writes remove_playlist_duplicates makes, to check the end result."""

    def __init__(self, sp, uris):
        self.uris = list(uris)
        sp.playlist_remove_all_occurrences_of_items.side_effect = self.remove
        sp.playlist_add_items.side_effect = self.add

    def remove(self, pid, uris):
        self.uris = [u for u in self.uris if u not in uris]

    def add(self, pid, uris, position):
        self.uris[position:position] = uris


class TestRemovePlaylistDuplicates:
    async def test_keeps_first_copy_in_place(self, sp):
        sp.playlist_items.return_value = _uri_entries("a", "b", "a", "c", "b", "a")
        result = await server.remove_playlist_duplicates("pl1")
        sp.playlist_remove_all_occurrences_of_items.assert_called_once_with("pl1", ["spotify:track:a", "spotify:track:b"])
        # Final playlist is a, b, c: re-insert a and b together at 0, in front of c.
        sp.playlist_add_items.assert_called_once_with("pl1", ["spotify:track:a", "spotify:track:b"], position=0)
        assert "Removed 3 extra copies of 2 track(s)" in result

    async def test_no_duplicates(self, sp):
        sp.playlist_items.return_value = _uri_entries("a", "b")
        result = await server.remove_playlist_duplicates("pl1")
        assert result == "No duplicate tracks found."
        sp.playlist_remove_all_occurrences_of_items.assert_not_called()

    async def test_removed_tracks_keep_their_place(self, sp):
        # A removed track (null) stays in the playlist, so positions have to count it.
        sp.playlist_items.return_value = _uri_entries(None, "a", "b", "a")
        playlist = FakePlaylist(sp, [None, "spotify:track:a", "spotify:track:b", "spotify:track:a"])
        await server.remove_playlist_duplicates("pl1")
        assert playlist.uris == [None, "spotify:track:a", "spotify:track:b"]

    async def test_reinserts_runs_of_positions_in_one_request(self, sp):
        ids = ["x", "a", "b", "c", "y", "d", "a", "b", "c", "d"]
        sp.playlist_items.return_value = _uri_entries(*ids)
        playlist = FakePlaylist(sp, [f"spotify:track:{i}" for i in ids])
        await server.remove_playlist_duplicates("pl1")
        assert [(c.kwargs["position"], len(c.args[1])) for c in sp.playlist_add_items.call_args_list] == [(1, 3), (5, 1)]
        assert playlist.uris == [f"spotify:track:{i}" for i in ["x", "a", "b", "c", "y", "d"]]

    async def test_restores_each_batch_before_the_next(self, sp):
        ids = [f"t{i:03}" for i in range(150)]
        sp.playlist_items.return_value = _uri_entries(*ids, *ids)
        playlist = FakePlaylist(sp, [f"spotify:track:{i}" for i in ids * 2])
        result = await server.remove_playlist_duplicates("pl1")
        assert [len(c.args[1]) for c in sp.playlist_remove_all_occurrences_of_items.call_args_list] == [100, 50]
        assert [len(c.args[1]) for c in sp.playlist_add_items.call_args_list] == [100, 50]
        assert playlist.uris == [f"spotify:track:{i}" for i in ids]
        assert "Removed 150 extra copies of 150 track(s)" in result

    async def test_failed_removal_leaves_completed_batches_whole(self, sp):
        ids = [f"t{i:03}" for i in range(150)]
        sp.playlist_items.return_value = _uri_entries(*ids, *ids)
        playlist = FakePlaylist(sp, [f"spotify:track:{i}" for i in ids * 2])
        remove = sp.playlist_remove_all_occurrences_of_items

        def remove_then_fail(pid, uris):
            if remove.call_count > 1:
                raise _error(500)
            playlist.remove(pid, uris)

        remove.side_effect = remove_then_fail
        with pytest.raises(SpotifyError, match="extra copies of 100 of 150 duplicated.*rest are unchanged"):
            await server.remove_playlist_duplicates("pl1")
        # The first batch lost only its extra copies; the second batch wasn't touched.
        assert playlist.uris == [f"spotify:track:{i}" for i in ids + ids[100:]]

    async def test_first_removal_failing_changes_nothing(self, sp):
        sp.playlist_items.return_value = _uri_entries("a", "a")
        sp.playlist_remove_all_occurrences_of_items.side_effect = _error(500)
        with pytest.raises(SpotifyError, match="HTTP 500"):
            await server.remove_playlist_duplicates("pl1")
        sp.playlist_add_items.assert_not_called()

    async def test_reports_tracks_it_could_not_restore(self, sp):
        sp.playlist_items.return_value = _uri_entries("a", "a")
        sp.playlist_add_items.side_effect = _error(500)
        with pytest.raises(SpotifyError, match="couldn't put back one copy of 1 track.*spotify:track:a"):
            await server.remove_playlist_duplicates("pl1")


# ---------------------------------------------------------------------------
# get_current_playing / get_queue / list_devices / transfer_playback
# ---------------------------------------------------------------------------

class TestGetCurrentPlaying:
    async def test_playing(self, sp):
        sp.current_playback.return_value = _playback(is_playing=True)
        result = await server.get_current_playing()
        state = data(result)
        assert state["is_playing"] is True
        assert state["item"]["name"] == "Track A"
        assert state["artist_uris"] == ["spotify:artist:bbb"]
        assert state["device"]["name"] == "Kitchen"
        assert state["progress_seconds"] == 61
        sp.current_playback.assert_called_once_with(additional_types="episode")

    async def test_paused(self, sp):
        sp.current_playback.return_value = _playback(is_playing=False)
        result = await server.get_current_playing()
        assert data(result)["is_playing"] is False

    async def test_nothing_playing(self, sp):
        sp.current_playback.return_value = None
        result = await server.get_current_playing()
        assert data(result) == {"is_playing": False}

    async def test_podcast_episode(self, sp):
        sp.current_playback.return_value = _playback() | {"item": _episode()}
        result = await server.get_current_playing()
        assert data(result)["item"] == {"name": "Episode A", "artists": ["Show A"], "uri": "spotify:episode:eee"}


class TestGetQueue:
    async def test_returns_queue(self, sp):
        sp.queue.return_value = {"currently_playing": _track(), "queue": [_track("Next", uri="spotify:track:n")]}
        result = await server.get_queue()
        assert data(result)["currently_playing"]["name"] == "Track A"
        assert data(result)["queue"][0]["name"] == "Next"

    async def test_empty_queue(self, sp):
        sp.queue.return_value = {"currently_playing": None, "queue": []}
        result = await server.get_queue()
        assert data(result) == {"queue": []}

    async def test_empty_response(self, sp):
        sp.queue.return_value = None
        result = await server.get_queue()
        assert data(result) == {"queue": []}


class TestListDevices:
    async def test_returns_devices(self, sp):
        sp.devices.return_value = {"devices": [
            {"id": "d1", "name": "Phone", "type": "Smartphone", "is_active": False, "volume_percent": 80},
        ]}
        result = await server.list_devices()
        assert data(result)["devices"][0] == {"id": "d1", "name": "Phone", "type": "Smartphone", "is_active": False, "volume_percent": 80}

    async def test_no_devices(self, sp):
        sp.devices.return_value = {"devices": []}
        result = await server.list_devices()
        assert data(result) == {"devices": []}


class TestTransferPlayback:
    async def test_transfers(self, sp):
        result = await server.transfer_playback("d1")
        sp.transfer_playback.assert_called_once_with("d1", force_play=True)
        assert result == "Playback moved to the device and is playing."

    async def test_transfer_keeps_playback_state(self, sp):
        # play=False means "keep the current state", not "pause".
        result = await server.transfer_playback("d1", play=False)
        sp.transfer_playback.assert_called_once_with("d1", force_play=False)
        assert "as it was before" in result
        assert "It's paused" not in result


# ---------------------------------------------------------------------------
# play_now / queue_tracks / queue_recommendations
# ---------------------------------------------------------------------------

class TestPlayNow:
    async def test_plays_tracks(self, sp):
        await server.play_now(["spotify:track:a", "spotify:episode:e"])
        sp.start_playback.assert_called_once_with(device_id=None, uris=["spotify:track:a", "spotify:episode:e"])

    async def test_plays_album_as_context(self, sp):
        await server.play_now(["https://open.spotify.com/album/abc"], device_id="d1")
        sp.start_playback.assert_called_once_with(device_id="d1", context_uri="spotify:album:abc")

    async def test_rejects_mixed_uris(self, sp):
        with pytest.raises(ToolError, match="either a single"):
            await server.play_now(["spotify:album:abc", "spotify:track:a"])
        sp.start_playback.assert_not_called()


class TestQueueTracks:
    async def test_queues_all_tracks(self, sp):
        result = await server.queue_tracks(["spotify:track:a", "spotify:track:b"])
        assert sp.add_to_queue.call_count == 2
        assert "Queued 2" in result

    async def test_deduplicates_uris(self, sp):
        await server.queue_tracks(["spotify:track:a", "spotify:track:a"])
        assert sp.add_to_queue.call_count == 1

    async def test_queues_episodes(self, sp):
        await server.queue_tracks(["spotify:episode:e"])
        sp.add_to_queue.assert_called_once_with("spotify:episode:e", device_id=None)

    async def test_queues_on_a_chosen_device(self, sp):
        # The no-active-device error says to pass a device_id, so the queue tools need one.
        await server.queue_tracks(["spotify:track:a"], device_id="d1")
        sp.add_to_queue.assert_called_once_with("spotify:track:a", device_id="d1")

    async def test_403_raises_premium_message(self, sp):
        sp.add_to_queue.side_effect = _error(403, message="Player command failed: Premium required")
        with pytest.raises(SpotifyError, match="Premium"):
            await server.queue_tracks(["spotify:track:a"])

    async def test_missing_track_is_skipped_not_a_device_error(self, sp):
        sp.add_to_queue.side_effect = [_error(404, message="Not found."), None]
        result = await server.queue_tracks(["spotify:track:gone", "spotify:track:b"])
        assert result == "Queued 1 track(s). 1 failed: spotify:track:gone"

    async def test_fatal_error_reports_progress(self, sp):
        sp.add_to_queue.side_effect = [None, _error(404, reason="NO_ACTIVE_DEVICE")]
        with pytest.raises(SpotifyError, match=r"No active Spotify device.*Queued 1 of 2"):
            await server.queue_tracks(["spotify:track:a", "spotify:track:b"])

    async def test_failed_tracks_reported(self, sp):
        sp.add_to_queue.side_effect = [None, _error(400, message="Invalid track uri")]
        result = await server.queue_tracks(["spotify:track:a", "spotify:track:b"])
        assert "Queued 1" in result
        assert "1 failed: spotify:track:b" in result


class TestQueueRecommendations:
    async def test_queues_with_query(self, sp):
        sp.search.return_value = {"tracks": _page([_track()])}
        result = await server.queue_recommendations(query="indie", count=1)
        assert "Queued 1" in result
        assert "Track A — Artist A" in result

    async def test_spreads_tracks_across_artists(self, sp):
        def search(q, **kwargs):
            artist = q.split('"')[1]
            return {"tracks": _page([_track(f"{artist} {i}", artist, f"spotify:track:{artist}{i}") for i in range(10)])}

        sp.search.side_effect = search
        result = await server.queue_recommendations(artists=["Folamour", 'Floating "Points'], count=4)
        # The searches run concurrently, so only the queue order is fixed.
        assert sorted(c.kwargs["q"] for c in sp.search.call_args_list) == ['artist:"Floating Points"', 'artist:"Folamour"']
        queued = [c.args[0] for c in sp.add_to_queue.call_args_list]
        assert queued == ["spotify:track:Folamour0", "spotify:track:Floating Points0",
                          "spotify:track:Folamour1", "spotify:track:Floating Points1"]
        assert "Queued 4" in result

    async def test_searches_only_as_many_artists_as_tracks_wanted(self, sp):
        sp.search.side_effect = lambda q, **kwargs: {"tracks": _page([_track(q, uri=f"spotify:track:{q}")])}
        await server.queue_recommendations(artists=[f"Artist {i}" for i in range(20)], count=3)
        assert sorted(c.kwargs["q"] for c in sp.search.call_args_list) == [
            'artist:"Artist 0"', 'artist:"Artist 1"', 'artist:"Artist 2"',
        ]
        assert sp.add_to_queue.call_count == 3

    async def test_falls_back_to_current_track(self, sp):
        sp.currently_playing.return_value = _playback()
        sp.search.return_value = {"tracks": _page([_track()])}
        result = await server.queue_recommendations(count=1)
        sp.currently_playing.assert_called_once_with(additional_types="episode")
        sp.search.assert_called_once_with(q="Track A Artist A", type="track", limit=10, offset=0)
        assert "Queued 1" in result

    async def test_podcast_playing_is_not_nothing_playing(self, sp):
        sp.currently_playing.return_value = {"currently_playing_type": "episode", "item": _episode()}
        result = await server.queue_recommendations()
        assert "a podcast is playing" in result
        sp.search.assert_not_called()

    async def test_queues_on_a_chosen_device(self, sp):
        sp.search.return_value = {"tracks": _page([_track()])}
        await server.queue_recommendations(query="indie", count=1, device_id="d1")
        sp.add_to_queue.assert_called_once_with("spotify:track:aaa", device_id="d1")

    async def test_limits_concurrent_artist_searches(self, sp, monkeypatch):
        running = peak = 0

        async def search(query, kind, limit):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return [_track(query, uri=f"spotify:track:{query}")]

        monkeypatch.setattr(server, "_search", search)
        await server.queue_recommendations(artists=[f"Artist {i}" for i in range(20)], count=20)
        assert peak == server.MAX_CONCURRENT_SEARCHES
        assert sp.add_to_queue.call_count == 20

    async def test_failed_artist_search_stops_the_others(self, sp, monkeypatch):
        started, cancelled = [], []

        async def search(query, kind, limit):
            started.append(query)
            if query == 'artist:"Artist 0"':
                raise SpotifyError("Spotify is rate limiting requests and asks to wait 3600s.")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(query)
                raise

        monkeypatch.setattr(server, "_search", search)
        with pytest.raises(SpotifyError, match="rate limiting"):
            # Without cancellation the other searches never finish, so time out instead of hanging.
            await asyncio.wait_for(server.queue_recommendations(artists=[f"Artist {i}" for i in range(20)], count=20), 5)
        assert len(started) < 20  # the rest never started
        assert sorted(cancelled) == sorted(started[1:])
        sp.add_to_queue.assert_not_called()

    async def test_nothing_playing_no_query(self, sp):
        sp.currently_playing.return_value = None
        result = await server.queue_recommendations()
        assert "No artists or query" in result

    async def test_no_tracks_found(self, sp):
        sp.search.return_value = {"tracks": _page([])}
        result = await server.queue_recommendations(query="xyzxyz")
        assert "No tracks found" in result

    async def test_deduplicates_results(self, sp):
        sp.search.return_value = {"tracks": _page([_track(), _track()])}
        await server.queue_recommendations(query="test", count=2)
        assert sp.add_to_queue.call_count == 1

    async def test_count_above_10_pages_search(self, sp):
        sp.search.side_effect = [
            {"tracks": _page([_track(uri=f"spotify:track:t{i}") for i in range(10)], "next")},
            {"tracks": _page([_track(uri=f"spotify:track:u{i}") for i in range(5)])},
        ]
        result = await server.queue_recommendations(query="test", count=15)
        assert [c.kwargs["offset"] for c in sp.search.call_args_list] == [0, 10]
        assert "Queued 15" in result


# ---------------------------------------------------------------------------
# control_playback
# ---------------------------------------------------------------------------

class TestControlPlayback:
    @pytest.mark.parametrize("action, method, label", [
        ("play", "start_playback", "resumed playback"),
        ("pause", "pause_playback", "paused"),
        ("skip_next", "next_track", "next track"),
        ("skip_previous", "previous_track", "previous track"),
    ])
    async def test_actions(self, sp, action, method, label):
        result = await server.control_playback(action=action)
        getattr(sp, method).assert_called_once_with(device_id=None)
        assert label in result

    async def test_settings_combined(self, sp):
        result = await server.control_playback(shuffle=True, repeat="context", volume_percent=30, seek_seconds=75, device_id="d1")
        sp.shuffle.assert_called_once_with(True, device_id="d1")
        sp.repeat.assert_called_once_with("context", device_id="d1")
        sp.volume.assert_called_once_with(30, device_id="d1")
        sp.seek_track.assert_called_once_with(75_000, device_id="d1")
        assert result == "Done: shuffle on, repeat context, volume 30%, jumped to 1:15."

    async def test_nothing_to_do(self, sp):
        with pytest.raises(ToolError, match="Nothing to do"):
            await server.control_playback()

    async def test_403_raises_premium_message(self, sp):
        sp.start_playback.side_effect = _error(403, reason="PREMIUM_REQUIRED")
        with pytest.raises(SpotifyError, match="Premium"):
            await server.control_playback(action="play")

    async def test_restriction_is_not_blamed_on_premium(self, sp):
        sp.previous_track.side_effect = _error(403, reason="UNKNOWN", message="Player command failed: Restriction violated")
        with pytest.raises(SpotifyError, match="Restriction violated") as excinfo:
            await server.control_playback(action="skip_previous")
        assert "Premium" not in str(excinfo.value)

    async def test_no_active_device(self, sp):
        sp.pause_playback.side_effect = _error(404, reason="NO_ACTIVE_DEVICE")
        with pytest.raises(SpotifyError, match="list_devices"):
            await server.control_playback(action="pause")

    async def test_unknown_device(self, sp):
        sp.pause_playback.side_effect = _error(404, message="Device not found")
        with pytest.raises(SpotifyError, match="No active Spotify device"):
            await server.control_playback(action="pause", device_id="gone")

    async def test_missing_context_is_not_a_device_error(self, sp):
        sp.start_playback.side_effect = _error(404, message="Non existing id: 'spotify:playlist:gone'")
        with pytest.raises(SpotifyError, match="Not found on Spotify") as excinfo:
            await server.play_now(["spotify:playlist:gone"])
        assert "device" not in str(excinfo.value)

    async def test_reports_what_was_done_before_failing(self, sp):
        sp.volume.side_effect = _error(403, reason="VOLUME_CONTROL_DISALLOW")
        with pytest.raises(SpotifyError, match=r"doesn't allow volume control.*Already done: shuffle on"):
            await server.control_playback(shuffle=True, volume_percent=50)


# ---------------------------------------------------------------------------
# _spotify: retries and error explanations
# ---------------------------------------------------------------------------

class TestSpotifyRetry:
    async def test_retries_on_429(self, sp):
        sp.search.side_effect = [_error(429, headers={"Retry-After": "0"}), "success"]
        with patch("server.asyncio.sleep") as mock_sleep:
            result = await server._spotify("search")
        assert result == "success"
        assert sp.search.call_count == 2
        mock_sleep.assert_called_once()

    async def test_does_not_retry_on_other_errors(self, sp):
        sp.search.side_effect = _error(404, message="Not found")
        with pytest.raises(SpotifyError):
            await server._spotify("search")
        assert sp.search.call_count == 1

    async def test_raises_after_max_attempts(self, sp):
        sp.search.side_effect = _error(429, headers={"Retry-After": "0"})
        with patch("server.asyncio.sleep"), pytest.raises(SpotifyError, match="rate limiting"):
            await server._spotify("search")
        assert sp.search.call_count == server.MAX_ATTEMPTS

    async def test_long_retry_after_fails_fast(self, sp):
        sp.search.side_effect = _error(429, headers={"Retry-After": "3600"})
        with patch("server.asyncio.sleep") as mock_sleep, pytest.raises(SpotifyError, match="wait 3600s"):
            await server._spotify("search")
        mock_sleep.assert_not_called()
        assert sp.search.call_count == 1


class TestSpotifyErrors:
    async def test_401_asks_to_sign_in(self, sp):
        sp.search.side_effect = _error(401, message="The access token expired")
        with pytest.raises(SpotifyError, match="auth.py"):
            await server._spotify("search")

    async def test_missing_login_asks_to_sign_in(self, sp):
        sp.search.side_effect = spotify_auth.LoginRequired()
        with pytest.raises(SpotifyError, match="(?s)no Spotify login is saved.*auth.py"):
            await server._spotify("search")

    async def test_expired_refresh_token_asks_to_sign_in(self, sp):
        sp.search.side_effect = SpotifyOauthError("x", error="invalid_grant", error_description="Refresh token revoked")
        with pytest.raises(SpotifyError, match="(?s)Refresh token revoked.*auth.py"):
            await server._spotify("search")

    async def test_bad_credentials(self, sp):
        sp.search.side_effect = SpotifyOauthError("x", error="invalid_client")
        with pytest.raises(SpotifyError, match="Client ID or Secret"):
            await server._spotify("search")
        assert server.sp is None  # rebuilt from .env on the next call, so a fix needs no restart

    async def test_network_error(self, sp):
        sp.search.side_effect = requests.exceptions.ConnectionError()
        with pytest.raises(SpotifyError, match="Couldn't reach Spotify"):
            await server._spotify("search")

    async def test_server_error(self, sp):
        sp.search.side_effect = _error(502)
        with pytest.raises(SpotifyError, match="HTTP 502"):
            await server._spotify("search")

    async def test_not_found_is_not_fatal(self, sp):
        sp.playlist.side_effect = _error(404, message="Invalid playlist Id")
        with pytest.raises(SpotifyError, match="Invalid playlist Id") as excinfo:
            await server._spotify("playlist", "x")
        assert excinfo.value.fatal is False

    async def test_playlist_not_owned(self, sp):
        sp.playlist_items.side_effect = _error(403, message="Forbidden")
        with pytest.raises(SpotifyError, match="owns or collaborates"):
            await server._spotify("playlist_items", "x")

    async def test_missing_credentials(self, monkeypatch):
        monkeypatch.setattr(server, "sp", None)
        monkeypatch.setattr(spotify_auth, "make_auth_manager", MagicMock(side_effect=SpotifyOauthError("No client_id")))
        with pytest.raises(SpotifyError, match="install.py"):
            await server._spotify("search")


@pytest.fixture
def fake_api(monkeypatch):
    """A local server standing in for api.spotify.com, behind a real client and _session().

    Set `.status` and `.headers` for its replies; `.requests` lists the HTTP methods it received.
    """
    class Handler(BaseHTTPRequestHandler):
        def reply(self):
            api.requests.append(self.command)
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.dumps({"error": {"status": api.status, "message": "Something went wrong"}}).encode()
            self.send_response(api.status)
            for name, value in api.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_DELETE = reply

        def log_message(self, format, *args):
            pass

    api = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    api.status, api.headers, api.requests = 503, {}, []
    threading.Thread(target=api.serve_forever, daemon=True).start()
    session = server._session()
    session.mount("http://", session.get_adapter("https://"))
    client = spotipy.Spotify(auth="token", requests_session=session)
    client.prefix = f"http://127.0.0.1:{api.server_address[1]}/"
    monkeypatch.setattr(server, "sp", client)
    with patch("urllib3.util.Retry.sleep"):  # skip urllib3's backoff between retries
        yield api
    api.shutdown()
    api.server_close()


class TestHttpRetries:
    async def test_server_errors_are_retried_on_reads_then_reported(self, fake_api):
        with pytest.raises(SpotifyError, match="having problems \\(HTTP 503\\)"):
            await server._spotify("current_user")
        assert fake_api.requests == ["GET"] * 4

    async def test_writes_are_not_sent_twice(self, fake_api):
        fake_api.status = 502
        with pytest.raises(SpotifyError, match="HTTP 502"):
            await server._spotify("playlist_add_items", "pl1", ["spotify:track:a"])
        assert fake_api.requests == ["POST"]

    async def test_rate_limits_reach_spotify_call_with_retry_after(self, fake_api):
        fake_api.status, fake_api.headers = 429, {"Retry-After": "3600"}
        with patch("server.asyncio.sleep") as mock_sleep, pytest.raises(SpotifyError, match="wait 3600s"):
            await server._spotify("current_user")
        assert fake_api.requests == ["GET"]
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Input parsing and output formatting
# ---------------------------------------------------------------------------

class TestRefs:
    @pytest.mark.parametrize("value", [
        "abc123",
        "spotify:playlist:abc123",
        "https://open.spotify.com/playlist/abc123?si=xyz",
        "https://open.spotify.com/intl-pt/playlist/abc123",
    ])
    def test_id_accepts_uris_urls_and_ids(self, value):
        assert server._id(value, "playlist") == "abc123"

    def test_id_rejects_other_types(self):
        with pytest.raises(ToolError, match="Expected a Spotify playlist, got a track"):
            server._id("spotify:track:abc", "playlist")

    def test_id_rejects_garbage(self):
        with pytest.raises(ToolError, match="Not a Spotify URI"):
            server._id("not an id!", "playlist")

    def test_uri_defaults_bare_ids_to_first_allowed_type(self):
        assert server._uri("abc", ("episode", "track")) == "spotify:episode:abc"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class TestLogging:
    def test_log_files_are_private(self, tmp_path):
        handler = server._PrivateRotatingFileHandler(tmp_path / "x.log", maxBytes=50, backupCount=1)
        handler.emit(logging.makeLogRecord({"msg": "first " * 20}))
        handler.emit(logging.makeLogRecord({"msg": "second"}))  # forces a rollover
        handler.close()
        for path in tmp_path.iterdir():
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, path

    @pytest.fixture
    def configure(self, tmp_path, monkeypatch):
        """Runs _configure_logging() into tmp_path, keeping the root handlers MCPServer() installed."""
        monkeypatch.setattr(server, "LOG_PATH", tmp_path / "server.log")
        root = logging.getLogger()
        loggers = [server.log, logging.getLogger("spotipy"), logging.getLogger("urllib3")]
        saved_handlers, saved_level = root.handlers[:], root.level
        saved_levels = [logger.level for logger in loggers]
        yield server._configure_logging
        for handler in root.handlers:
            if handler not in saved_handlers:
                handler.close()
        root.handlers, root.level = saved_handlers, saved_level
        for logger, level in zip(loggers, saved_levels):
            logger.setLevel(level)

    def test_server_logs_reach_the_file_but_library_debug_output_does_not(self, tmp_path, configure):
        configure()
        server.log.info("hello from the server")
        logging.getLogger("spotipy.client").debug("Headers: {'Authorization': 'Bearer secret-token'}")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = (tmp_path / "server.log").read_text()
        assert "hello from the server" in text
        assert "secret-token" not in text
        assert stat.S_IMODE(os.stat(tmp_path / "server.log").st_mode) == 0o600

    def test_makes_logs_from_older_versions_private(self, tmp_path, configure):
        # Older versions logged tokens at DEBUG, into world-readable files that were then rotated.
        old = [tmp_path / "server.log", tmp_path / "server.log.1", tmp_path / "server.log.3"]
        for path in old:
            path.write_text("Authorization: Basic secret")
            path.chmod(0o644)
        configure()
        for path in old:
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, path

    def test_unknown_log_level_falls_back_to_info(self, configure, monkeypatch):
        monkeypatch.setenv("MCPOTIFY_LOG_LEVEL", "verbose")
        configure()
        assert server.log.level == logging.INFO


# ---------------------------------------------------------------------------
# The server as an MCP client sees it
# ---------------------------------------------------------------------------

class TestMcpServer:
    async def test_tool_annotations(self):
        tools = {t.name: t for t in await server.mcp.list_tools()}
        read_only = {name for name, t in tools.items() if t.annotations.read_only_hint}
        destructive = {name for name, t in tools.items() if t.annotations.destructive_hint}
        assert {"search_tracks", "get_playlist_tracks", "find_playlist_issues", "get_queue"} <= read_only
        assert destructive == {"delete_playlist", "remove_from_playlist", "remove_from_library", "remove_playlist_duplicates"}
        assert all(t.title for t in tools.values())

    async def test_structured_output_over_the_wire(self, sp):
        sp.search.return_value = {"tracks": _page([_track()])}
        async with Client(server.mcp) as client:
            result = await client.call_tool("search_tracks", {"query": "x"})
        assert result.structured_content["tracks"][0]["uri"] == "spotify:track:aaa"

    async def test_errors_reach_the_model(self, sp):
        sp.add_to_queue.side_effect = _error(403, reason="PREMIUM_REQUIRED")
        async with Client(server.mcp) as client:
            result = await client.call_tool("queue_tracks", {"track_uris": ["spotify:track:a"]})
        assert result.is_error
        assert "Premium" in result.content[0].text

    async def test_invalid_arguments_reach_the_model(self):
        async with Client(server.mcp) as client:
            result = await client.call_tool("search_tracks", {"query": "x", "limit": 500})
        assert result.is_error
        assert "less than or equal to 50" in result.content[0].text

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    @pytest.mark.parametrize("answer, deleted", [("accept", True), ("decline", False)])
    async def test_delete_asks_the_user(self, sp, mode, answer, deleted):
        sp.playlist.return_value = {"name": "Road Trip"}
        questions = []

        async def on_elicit(context, params):
            questions.append(params.message)
            return ElicitResult(action=answer, content={"confirm": True} if answer == "accept" else None)

        async with Client(server.mcp, mode=mode, elicitation_callback=on_elicit) as client:
            result = await client.call_tool("delete_playlist", {"playlist_id": "pl1"})
        assert "Road Trip" in questions[0]
        assert sp._delete.called is deleted
        assert not result.is_error

    async def test_vibe_prompt(self):
        prompt = await server.mcp.get_prompt("vibe", {"mood": "rainy sunday", "destination": "playlist"})
        text = prompt.messages[0].content.text
        assert "rainy sunday" in text
        assert "create the playlist" in text

    async def test_tool_results_warn_before_login_expires(self, sp, monkeypatch):
        sp.search.return_value = {"tracks": _page([_track()])}
        monkeypatch.setattr(spotify_auth, "login_expires_at", lambda: time.time() + 3 * 86400)
        async with Client(server.mcp) as client:
            read = await client.call_tool("search_tracks", {"query": "x"})
            write = await client.call_tool("queue_tracks", {"track_uris": ["spotify:track:a"]})
        assert "auth.py" in read.content[-1].text
        assert read.structured_content["tracks"][0]["uri"] == "spotify:track:aaa"
        assert "auth.py" in write.content[-1].text

    async def test_no_login_warning_long_before_expiry(self, sp, monkeypatch):
        monkeypatch.setattr(spotify_auth, "login_expires_at", lambda: time.time() + 100 * 86400)
        async with Client(server.mcp) as client:
            result = await client.call_tool("queue_tracks", {"track_uris": ["spotify:track:a"]})
        assert [c.text for c in result.content] == ["Queued 1 track(s)."]

    async def test_login_warning_stops_after_renewal(self, sp, monkeypatch):
        expires_at = time.time() + 3 * 86400
        monkeypatch.setattr(spotify_auth, "login_expires_at", lambda: expires_at)
        async with Client(server.mcp) as client:
            near = await client.call_tool("queue_tracks", {"track_uris": ["spotify:track:a"]})
            expires_at = time.time() + 182 * 86400
            renewed = await client.call_tool("queue_tracks", {"track_uris": ["spotify:track:a"]})
        assert "auth.py" in near.content[-1].text
        assert len(renewed.content) == 1

    async def test_login_warning_skips_errors(self, sp, monkeypatch):
        monkeypatch.setattr(spotify_auth, "login_expires_at", lambda: time.time() + 3 * 86400)
        sp.add_to_queue.side_effect = _error(403, reason="PREMIUM_REQUIRED")
        async with Client(server.mcp) as client:
            result = await client.call_tool("queue_tracks", {"track_uris": ["spotify:track:a"]})
        assert result.is_error
        assert len(result.content) == 1

    async def test_login_warning_keeps_elicitation_working(self, sp, monkeypatch):
        monkeypatch.setattr(spotify_auth, "login_expires_at", lambda: time.time() + 3 * 86400)
        sp.playlist.return_value = {"name": "Road Trip"}

        async def on_elicit(context, params):
            return ElicitResult(action="accept", content={"confirm": True})

        async with Client(server.mcp, elicitation_callback=on_elicit) as client:
            result = await client.call_tool("delete_playlist", {"playlist_id": "pl1"})
        assert sp._delete.called
        assert "auth.py" in result.content[-1].text
