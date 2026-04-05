"""
Patch the Spotify client before server.py is imported so the module-level
SpotifyOAuth flow never runs during tests.
"""
import pytest
from unittest.mock import MagicMock, patch

_mock_sp = MagicMock()

with patch("spotipy.Spotify", return_value=_mock_sp), \
     patch("spotipy.oauth2.SpotifyOAuth", return_value=MagicMock()):
    import server  # noqa: E402  (import not at top of file is intentional)

# Ensure server.sp points to our controllable mock
server.sp = _mock_sp


@pytest.fixture(autouse=True)
def reset_sp():
    """Reset the Spotify mock before every test so calls and side_effects don't bleed."""
    server.sp.reset_mock(side_effect=True, return_value=True)

