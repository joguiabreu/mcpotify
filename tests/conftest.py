"""
Every test gets a fresh mock Spotify client, and a login cache path inside the
test's temp dir so nothing reads or writes the real .cache.
"""
from unittest.mock import MagicMock

import pytest

import server
import spotify_auth


@pytest.fixture(autouse=True)
def sp(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(server, "sp", mock)
    return mock


@pytest.fixture(autouse=True)
def isolated_login_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(spotify_auth, "CACHE_PATH", tmp_path / ".cache")
    return tmp_path / ".cache"
