#!/usr/bin/env python3
"""
One-command setup for mcp-spotify. Safe to re-run: it skips steps that are already done.

Usage:
    python install.py
"""
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
IS_WINDOWS = platform.system() == "Windows"
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
PIP = VENV / ("Scripts/pip.exe" if IS_WINDOWS else "bin/pip")
ENV_FILE = ROOT / ".env"


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
            ENV_FILE.chmod(0o600)  # it holds the client secret
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

    ENV_FILE.touch(mode=0o600)
    ENV_FILE.chmod(0o600)  # it holds the client secret
    ENV_FILE.write_text(
        f"SPOTIFY_CLIENT_ID={client_id}\n"
        f"SPOTIFY_CLIENT_SECRET={client_secret}\n"
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
    print("  If a browser window opens, approve access and wait for the confirmation page.\n")
    # auth.py skips the browser when a valid login with every permission is already saved.
    if subprocess.run([str(PYTHON), str(ROOT / "auth.py")], cwd=ROOT).returncode != 0:
        _fail("Spotify sign-in didn't complete. Fix the problem above and re-run install.py.")


def _find_claude():
    """Return the path to the Claude Code CLI, or None if it can't be found.

    Falls back to the binary bundled with the VS Code / Cursor / Windsurf
    extension, since extension-only installs don't put `claude` on PATH.
    """
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    exe = "claude.exe" if IS_WINDOWS else "claude"
    candidates = []
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        ext_dir = Path.home() / editor / "extensions"
        candidates += ext_dir.glob(f"anthropic.claude-code-*/resources/native-binary/{exe}")
    if not candidates:
        return None
    # Prefer the most recently installed extension version
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def _shell_join(args):
    return subprocess.list2cmdline(args) if IS_WINDOWS else shlex.join(args)


def register_mcp():
    _step("Registering MCP server with Claude Code")
    add_args = ["mcp", "add", "--scope", "user", "spotify",
                str(PYTHON), str(ROOT / "server.py")]

    def print_manual(claude="claude"):
        print("\n  To register it yourself, run:\n")
        print(f"  {_shell_join([claude] + add_args)}\n")

    claude = _find_claude()
    if not claude:
        _warn("Claude Code CLI not found.")
        print("  Install it from https://claude.com/claude-code, or skip this if you use another AI app.")
        print_manual()
        return

    if subprocess.run([claude, "mcp", "get", "spotify"], capture_output=True).returncode == 0:
        _ok("MCP server already registered")
        return

    answer = input("  Register the Spotify server with Claude Code now? [Y/n] ").strip().lower()
    if answer not in ("", "y", "yes"):
        print_manual(claude)
        return

    result = subprocess.run([claude] + add_args, capture_output=True, text=True)
    if result.returncode == 0:
        _ok("MCP server registered")
    else:
        _warn("Could not register automatically:")
        print(f"  {(result.stderr or result.stdout).strip()}")
        print_manual(claude)


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
