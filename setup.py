#!/usr/bin/env python3
"""
One-command setup for mcp-spotify.

Usage:
    python setup.py
"""
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
IS_WINDOWS = platform.system() == "Windows"
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
PIP = VENV / ("Scripts/pip.exe" if IS_WINDOWS else "bin/pip")
ENV_FILE = ROOT / ".env"
CACHE_FILE = ROOT / ".cache"


# --- Output helpers ---

def _header(msg):
    print(f"\n{'='*50}\n  {msg}\n{'='*50}")

def _step(msg):
    print(f"\n→ {msg}")

def _ok(msg):
    print(f"  ✓ {msg}")

def _warn(msg):
    print(f"  ! {msg}")

def _fail(msg):
    print(f"\n  ✗ {msg}")
    sys.exit(1)


# --- Setup steps ---

def check_python():
    _step("Checking Python version")
    if sys.version_info < (3, 10):
        _fail(f"Python 3.10+ required (found {sys.version.split()[0]})")
    _ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")


def setup_env():
    _step("Spotify credentials")

    if ENV_FILE.exists():
        reconfigure = input("  .env already exists. Reconfigure? [y/N] ").strip().lower()
        if reconfigure != "y":
            _ok("Keeping existing .env")
            return

    print()
    print("  You need a Spotify app to get these credentials.")
    print("  Create one at: https://developer.spotify.com/dashboard")
    print("  Set the redirect URI to: http://127.0.0.1:8888/callback")
    print()

    client_id = input("  Client ID:     ").strip()
    client_secret = input("  Client Secret: ").strip()

    if not client_id or not client_secret:
        _fail("Client ID and Client Secret are required.")

    ENV_FILE.write_text(
        f"SPOTIFY_CLIENT_ID={client_id}\n"
        f"SPOTIFY_CLIENT_SECRET={client_secret}\n"
        f"SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback\n"
    )
    _ok(".env written")


def setup_venv():
    _step("Virtual environment")
    if not VENV.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        _ok("Created .venv")
    else:
        _ok(".venv already exists")


def install_deps():
    _step("Installing dependencies")
    subprocess.run(
        [str(PIP), "install", "-q", "-r", str(ROOT / "requirements.txt")],
        check=True,
    )
    _ok("Dependencies installed")


def run_auth():
    _step("Spotify authentication")
    if CACHE_FILE.exists():
        _ok("Already authenticated (.cache exists)")
        return
    print("  A browser window will open — approve access and wait for confirmation.\n")
    subprocess.run([str(PYTHON), str(ROOT / "auth.py")], check=True)


def register_mcp():
    _step("Registering MCP server with Claude Code")
    result = subprocess.run(
        ["claude", "mcp", "add", "--scope", "user", "spotify",
         str(PYTHON), str(ROOT / "server.py")],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        _ok("MCP server registered")
    elif "already" in (result.stdout + result.stderr).lower():
        _ok("MCP server already registered")
    else:
        _warn("Could not register automatically. Run this manually:")
        print(f"\n  claude mcp add --scope user spotify {PYTHON} {ROOT / 'server.py'}\n")


# --- Entry point ---

def main():
    _header("mcp-spotify setup")

    check_python()
    setup_env()
    setup_venv()
    install_deps()
    run_auth()
    register_mcp()

    print("\n" + "=" * 50)
    print("  Setup complete!")
    print("  Restart Claude Code to activate the Spotify tools.")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    main()
