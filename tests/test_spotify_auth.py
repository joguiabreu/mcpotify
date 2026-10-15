import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from spotipy.oauth2 import SpotifyOauthError

import auth
import spotify_auth


def _write_cache(path, **token_info):
    token = {"access_token": "a", "refresh_token": "r", "expires_at": 0, "scope": " ".join(spotify_auth.SCOPES)}
    path.write_text(json.dumps(token | token_info))


@pytest.fixture
def credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(spotify_auth, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")


class TestAuthManager:
    def test_server_auth_never_prompts(self, credentials):
        manager = spotify_auth.make_auth_manager()
        with pytest.raises(spotify_auth.LoginRequired):
            manager.get_auth_response()

    def test_uses_shared_settings(self, credentials, isolated_login_cache):
        manager = spotify_auth.make_auth_manager(interactive=True)
        assert manager.redirect_uri == "http://127.0.0.1:8888/callback"
        assert set(manager.scope.split()) == set(spotify_auth.SCOPES)
        assert manager.cache_handler.cache_path == str(isolated_login_cache)

    def test_missing_credentials(self, monkeypatch, tmp_path):
        monkeypatch.setattr(spotify_auth, "ENV_PATH", tmp_path / ".env")
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.delenv("SPOTIPY_CLIENT_ID", raising=False)
        with pytest.raises(SpotifyOauthError):
            spotify_auth.make_auth_manager()

    def test_rereads_env_file(self, monkeypatch, tmp_path):
        # install.py can fix the credentials while the server runs; the next auth manager sees them.
        env = tmp_path / ".env"
        monkeypatch.setattr(spotify_auth, "ENV_PATH", env)
        for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
            monkeypatch.delenv(name, raising=False)
        env.write_text("SPOTIFY_CLIENT_ID=typo\nSPOTIFY_CLIENT_SECRET=wrong\n")
        assert spotify_auth.make_auth_manager().client_secret == "wrong"
        env.write_text("SPOTIFY_CLIENT_ID=id\nSPOTIFY_CLIENT_SECRET=right\n")
        assert spotify_auth.make_auth_manager().client_id == "id"
        assert spotify_auth.make_auth_manager().client_secret == "right"

    def test_environment_wins_over_env_file(self, credentials):
        spotify_auth.ENV_PATH.write_text("SPOTIFY_CLIENT_ID=from-file\nSPOTIFY_CLIENT_SECRET=from-file\n")
        assert spotify_auth.make_auth_manager().client_id == "id"

    def test_has_valid_login_refreshes_even_an_unexpired_token(self, credentials, isolated_login_cache):
        # Only a refresh shows that the user revoked access; the access token alone looks fine.
        _write_cache(isolated_login_cache, expires_at=time.time() + 3600)
        manager = spotify_auth.make_auth_manager(interactive=True)
        manager.refresh_access_token = MagicMock()
        assert spotify_auth.has_valid_login(manager)
        manager.refresh_access_token.assert_called_once_with("r")

    def test_revoked_login_is_not_valid(self, credentials, isolated_login_cache):
        _write_cache(isolated_login_cache, expires_at=time.time() + 3600)
        manager = spotify_auth.make_auth_manager(interactive=True)
        manager.refresh_access_token = MagicMock(side_effect=SpotifyOauthError("x", error="invalid_grant"))
        assert not spotify_auth.has_valid_login(manager)

    def test_login_missing_scopes_or_absent_is_not_valid(self, credentials, isolated_login_cache):
        manager = spotify_auth.make_auth_manager(interactive=True)
        manager.refresh_access_token = MagicMock()
        assert not spotify_auth.has_valid_login(manager)
        _write_cache(isolated_login_cache, scope="user-top-read")
        assert not spotify_auth.has_valid_login(manager)
        manager.refresh_access_token.assert_not_called()

    def test_wrong_credentials_are_not_a_bad_login(self, credentials, isolated_login_cache):
        _write_cache(isolated_login_cache)
        manager = spotify_auth.make_auth_manager(interactive=True)
        manager.refresh_access_token = MagicMock(side_effect=SpotifyOauthError("x", error="invalid_client"))
        with pytest.raises(SpotifyOauthError):
            spotify_auth.has_valid_login(manager)

    def test_auth_command_points_at_auth_script(self):
        assert str(spotify_auth.ROOT / "auth.py") in spotify_auth.auth_command()


class TestLoginCache:
    def test_refresh_keeps_authorization_time(self, isolated_login_cache):
        _write_cache(isolated_login_cache, authorized_at=1000)
        cache = spotify_auth._LoginCache(cache_path=str(isolated_login_cache))
        cache.save_token_to_cache({"access_token": "new", "scope": "x"})
        assert cache.get_cached_token()["authorized_at"] == 1000

    def test_record_login(self, credentials, isolated_login_cache):
        _write_cache(isolated_login_cache)
        spotify_auth.record_login(spotify_auth.make_auth_manager())
        authorized_at = json.loads(isolated_login_cache.read_text())["authorized_at"]
        assert abs(authorized_at - time.time()) < 5

    def test_expiry_is_six_months_after_login(self, isolated_login_cache):
        _write_cache(isolated_login_cache, authorized_at=1000)
        assert spotify_auth.login_expires_at() == 1000 + 182 * 86400

    def test_expiry_unknown_without_authorization_time(self, isolated_login_cache):
        assert spotify_auth.login_expires_at() is None
        _write_cache(isolated_login_cache)
        assert spotify_auth.login_expires_at() is None
        assert not spotify_auth.login_renewal_due()

    def test_renewal_due_in_the_last_days(self, isolated_login_cache):
        lifetime = spotify_auth.LOGIN_LIFETIME_DAYS * 86400
        _write_cache(isolated_login_cache, authorized_at=time.time() - lifetime + 3 * 86400)
        assert spotify_auth.login_renewal_due()
        _write_cache(isolated_login_cache, authorized_at=time.time() - lifetime + 100 * 86400)
        assert not spotify_auth.login_renewal_due()


class TestCachedLoginProblem:
    def test_no_login(self):
        assert spotify_auth.cached_login_problem() == "no Spotify login is saved"

    def test_missing_scopes(self, isolated_login_cache):
        _write_cache(isolated_login_cache, scope="playlist-read-private user-top-read")
        problem = spotify_auth.cached_login_problem()
        assert "user-library-read" in problem
        assert "user-top-read" not in problem

    def test_ok(self, isolated_login_cache):
        _write_cache(isolated_login_cache)
        assert spotify_auth.cached_login_problem() is None


class TestAuthCallback:
    @pytest.fixture
    def port(self, monkeypatch):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        monkeypatch.setattr(spotify_auth, "REDIRECT_PORT", free)
        return free

    def _wait_in_background(self, state):
        result = {}
        callback = auth.CallbackServer(state)  # listening from here on
        thread = threading.Thread(target=lambda: result.update(code=callback.wait()))
        thread.start()
        return thread, result

    def _get(self, port, path):
        try:
            return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_returns_code_and_ignores_other_paths(self, port):
        thread, result = self._wait_in_background("s1")
        assert self._get(port, "/favicon.ico") == 404
        assert self._get(port, "/callback?code=abc&state=s1") == 200
        thread.join(5)
        assert result["code"] == "abc"

    def test_wrong_state_is_rejected_but_keeps_waiting(self, port):
        thread, result = self._wait_in_background("s1")
        assert self._get(port, "/callback?code=old&state=stale") == 400
        assert thread.is_alive()
        assert self._get(port, "/callback?code=abc&state=s1") == 200
        thread.join(5)
        assert result["code"] == "abc"

    def test_spotify_error_ends_the_wait(self, port):
        thread, result = self._wait_in_background("s1")
        assert self._get(port, "/callback?error=access_denied&state=s1") == 400
        thread.join(5)
        assert result["code"] is None

    def test_listens_before_opening_the_browser(self, port, monkeypatch):
        # A user who approved the app before is redirected straight back, before auth.py waits.
        manager = MagicMock()
        manager.get_authorize_url.side_effect = lambda state: f"https://accounts.spotify.com/authorize?state={state}"
        monkeypatch.setattr(spotify_auth, "make_auth_manager", lambda interactive: manager)
        monkeypatch.setattr(spotify_auth, "record_login", MagicMock())
        monkeypatch.setattr(spotify_auth, "login_expires_at", time.time)
        monkeypatch.setattr(sys, "argv", ["auth.py", "--force"])
        connections = []

        def redirect_at_once(url):
            state = parse_qs(urlparse(url).query)["state"][0]
            connection = socket.create_connection(("127.0.0.1", port))  # refused if nothing listens yet
            connection.sendall(f"GET /callback?code=abc&state={state} HTTP/1.0\r\n\r\n".encode())
            connections.append(connection)

        monkeypatch.setattr(auth.webbrowser, "open", redirect_at_once)
        try:
            assert auth.main() == 0
        finally:
            for connection in connections:
                connection.close()
        manager.get_access_token.assert_called_once_with("abc", as_dict=False, check_cache=False)


class TestAuthScript:
    @pytest.fixture
    def manager(self, monkeypatch):
        manager = MagicMock()
        manager.get_authorize_url.return_value = "https://accounts.spotify.com/authorize"
        monkeypatch.setattr(spotify_auth, "make_auth_manager", lambda interactive: manager)
        monkeypatch.setattr(spotify_auth, "record_login", MagicMock())
        monkeypatch.setattr(auth.webbrowser, "open", MagicMock())
        return manager

    @pytest.fixture
    def redirect(self, monkeypatch):
        """Spotify redirects back with a code as soon as auth.py listens."""
        callback = MagicMock()
        callback.wait.return_value = "abc"
        monkeypatch.setattr(auth, "CallbackServer", lambda state: callback)

    def _run(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["auth.py", *args])
        return auth.main()

    def test_keeps_a_valid_login(self, manager, monkeypatch, capsys):
        monkeypatch.setattr(spotify_auth, "has_valid_login", lambda m: True)
        assert self._run(monkeypatch) == 0
        assert "Already signed in" in capsys.readouterr().out
        auth.webbrowser.open.assert_not_called()

    def test_renews_a_login_about_to_expire(self, manager, redirect, monkeypatch, isolated_login_cache, capsys):
        # The server tells the user to run auth.py (without --force) to renew; that has to work.
        lifetime = spotify_auth.LOGIN_LIFETIME_DAYS * 86400
        _write_cache(isolated_login_cache, authorized_at=time.time() - lifetime + 3 * 86400)
        monkeypatch.setattr(spotify_auth, "has_valid_login", lambda m: True)
        assert self._run(monkeypatch) == 0
        assert "about to expire" in capsys.readouterr().out
        manager.get_access_token.assert_called_once_with("abc", as_dict=False, check_cache=False)

    def test_wrong_client_secret(self, manager, redirect, monkeypatch, capsys):
        manager.get_access_token.side_effect = SpotifyOauthError("x", error="invalid_client")
        assert self._run(monkeypatch, "--force") == 1
        assert "Re-run install.py and choose to reconfigure" in capsys.readouterr().out

    def test_spotify_unreachable(self, manager, monkeypatch, capsys):
        def offline(m):
            raise requests.exceptions.ConnectionError("no network")

        monkeypatch.setattr(spotify_auth, "has_valid_login", offline)
        assert self._run(monkeypatch) == 1
        assert "Couldn't reach Spotify (ConnectionError)" in capsys.readouterr().out

    def test_redirect_port_in_use(self, manager, monkeypatch, capsys):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            monkeypatch.setattr(spotify_auth, "REDIRECT_PORT", taken.getsockname()[1])
            assert self._run(monkeypatch, "--force") == 1
        assert "Close whatever is using port" in capsys.readouterr().out
        auth.webbrowser.open.assert_not_called()
