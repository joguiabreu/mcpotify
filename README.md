# Spotify MCP

Control Spotify through your AI assistant using plain language — ask in any language your AI supports. Search for music, build playlists, manage your queue, and control playback — just by asking.

> *"Make me a dark, rainy day playlist with 20 tracks"*
> *"Queue something similar to what I'm listening to right now"*
> *"Add the top tracks from Radiohead's discography to my Rock playlist"*
> *"Pause the music"*

Once set up, your AI assistant can do all of this directly in your Spotify account.

**What is MCP?** MCP (Model Context Protocol) is an open standard that lets AI assistants take actions on your behalf — in this case, controlling Spotify. Think of it as giving your AI a set of hands.

**What this is NOT:** The AI only gets access to your Spotify — no passwords, no payment details, no other accounts. You can revoke access at any time from your Spotify account settings.

<!-- TODO: add a short GIF here showing a prompt being typed and a playlist appearing in Spotify. Record with QuickTime (Mac) or Xbox Game Bar (Windows) and drop the file into this folder. -->

---

## What you can ask

### Playlists
- *"Create a playlist called Friday Night with 15 upbeat house tracks"*
- *"Add 10 songs similar to Daft Punk to my existing workout playlist"*
- *"Show me what's in my Chill Vibes playlist"*
- *"Rename my study playlist to Deep Focus and move the last song to the top"*
- *"Check my playlists for duplicates and songs that no longer play"*

### Queue
- *"Queue 10 tracks that match the vibe of what I'm listening to"*
- *"Find some dreamy shoegaze songs and add them to my queue"*
- *"What's coming up in my queue?"*

### Playback
- *"Pause"* / *"Resume"* / *"Skip this"* / *"Go back"*
- *"Play the album Currents now"* / *"Shuffle on and volume to 30%"*
- *"Move the music to the kitchen speaker"*

### Your library
- *"Like this song"*
- *"What did I listen to this morning? Queue more like that"*
- *"Show me my most recently liked songs"*

### Podcasts
- *"Find a podcast about Roman history"*
- *"Play the latest episode of The Rest Is History"*

### Discovery & browsing
- *"Search for melancholic jazz from the 60s"*
- *"Show me all albums by Nick Cave"*
- *"What are my most listened-to artists this month?"*
- *"What am I listening to right now?"*

---

## Compatible AI apps

| App | Supported | Notes |
|---|---|---|
| Claude Code | Yes | Automatic setup via `install.py` |
| ChatGPT desktop app | Yes | Manual setup required — see below |
| Cursor | Yes | Manual setup required — see below |
| Windsurf | Yes | Manual setup required — see below |
| Claude.ai (browser) | No | Browser-based Claude does not support MCP |

> The quality of results depends on the AI model. Models that are better at following multi-step instructions will tend to produce better results.

---

## What you need

- A **Spotify Premium account**. Since February 2026, Spotify only lets Premium subscribers create the developer "app" in Step 2, and the app stops working if that subscription lapses. Queue and playback control also require Premium.
- **Python 3.10 or later**
- One of the compatible AI apps listed above

**Installing Python:**
- **Mac:** Open Terminal and run `python3 --version`. If you see a version below 3.10 or get an error, download the latest from [python.org](https://www.python.org/downloads/). Run the installer and you're done.
- **Windows:** Download from [python.org](https://www.python.org/downloads/). During install, **check the box that says "Add Python to PATH"** — this is easy to miss.

---

## Setup

### Step 1 — Download the project

Download this project and unzip it somewhere on your computer (e.g. your Desktop or Documents folder).

**How to unzip:**
- **Mac:** Double-click the downloaded `.zip` file. A folder called `mcp-spotify` will appear next to it — that's the one you want.
- **Windows:** Right-click the `.zip` file and select **Extract All**, then click **Extract**. Make sure you're working from the extracted folder, not from inside the zip file.

### Step 2 — Give Spotify permission

You need to register a small "app" on Spotify's side so it knows your AI assistant is allowed to access your account. This is free, takes about 2 minutes, and does not affect your Spotify experience in any way.

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and log in with your Spotify account.
2. Click **Create app**.
3. Fill in the form:
   - **App name:** anything you like, e.g. `My Spotify MCP`
   - **App description:** anything, e.g. `Personal use`
   - **Redirect URI:** paste this exactly — `http://127.0.0.1:8888/callback`
   - Check the **Web API** checkbox
4. Click **Save**.
5. Click **Settings** (top right of the next page).
6. Copy your **Client ID**.
7. Click **View client secret** and copy that too.

### Step 3 — Run the setup script

Open a **Terminal** (Mac) or **Command Prompt** (Windows) and run the following commands, replacing `mcp-spotify` with the actual folder name if it's different:

**Mac:**
```
cd ~/Desktop/mcp-spotify
python3 install.py
```

**Windows:**
```
cd %USERPROFILE%\Desktop\mcp-spotify
python3 install.py
```

> If you unzipped the project somewhere other than your Desktop, replace `Desktop/mcp-spotify` with the actual path to your folder, e.g. `Documents/mcp-spotify`.

The script will:
- Ask for your Client ID and Client Secret from Step 2
- Install everything automatically
- Open a browser to log in to Spotify (approve access and wait for the confirmation page)
- Offer to register the server with Claude Code for you — press Enter to accept

If you decline, or registration fails, the script prints the exact command to copy into your terminal instead.

That's it — restart Claude Code (or reload the VS Code window) and you're ready to go.

---

## Connecting to other AI apps

> **Not sure which to choose?** Claude Code has automatic setup and is the easiest option. If you're comfortable with editing config files, you can use any of the apps below.

If you use ChatGPT, Cursor, or Windsurf instead of Claude Code, you'll need to register the server manually after running `install.py`. You'll need to know the full path to the project folder on your computer.

**How to find the full path:**
- **Mac:** Open Terminal, drag the project folder into the Terminal window, and copy the path that appears.
- **Windows:** Open the project folder in File Explorer, click the address bar at the top, and copy the full path shown.

**ChatGPT desktop app:**
Go to Settings → MCP Servers and add a new server:
- Command: the full path to `.venv/bin/python` inside the project folder
- Arguments: the full path to `server.py` inside the project folder

For example, if your project is on the Desktop:
- Mac: command `~/Desktop/mcp-spotify/.venv/bin/python`, arguments `~/Desktop/mcp-spotify/server.py`
- Windows: command `%USERPROFILE%\Desktop\mcp-spotify\.venv\Scripts\python.exe`, arguments `%USERPROFILE%\Desktop\mcp-spotify\server.py`

**Cursor / Windsurf:**
The config file for Cursor is `.cursor/mcp.json`. Its full location is:
- **Mac:** `~/.cursor/mcp.json` (i.e. `/Users/yourname/.cursor/mcp.json`)
- **Windows:** `C:\Users\yourname\.cursor\mcp.json`

Files and folders starting with `.` are hidden by default. To reveal them:
- **Mac:** In Finder, press `Cmd + Shift + .`
- **Windows:** In File Explorer, go to View → Show → Hidden items

If the file doesn't exist yet, create it: open Notepad (Windows) or TextEdit (Mac, set to plain text mode via Format → Make Plain Text), paste the full block below, and save it as `mcp.json` in the `.cursor` folder. Make sure your editor doesn't add `.txt` to the end of the filename.

Paste the following, replacing the paths with your actual project location:
```json
{
  "mcpServers": {
    "spotify": {
      "command": "/Users/yourname/Desktop/mcp-spotify/.venv/bin/python",
      "args": ["/Users/yourname/Desktop/mcp-spotify/server.py"]
    }
  }
}
```

---

## Built-in guidance

The server tells your AI app how to use the Spotify tools well (for example, how to turn a mood into a search that actually works, and when to ask before changing a playlist). This works in every app and every folder; there's nothing to install.

It also offers a **vibe** prompt for finding music by mood. In Claude Code, type:

> `/mcp__spotify__vibe "rainy sunday morning"`

Add `playlist` after the mood to get a playlist instead of queued tracks.

---

## Try it out

Open your AI app and try one of these to get started:

> *"What am I currently listening to on Spotify?"*
> *"Search for some late night lo-fi tracks and make a playlist called Late Night"*
> *"What are my top artists from the last month?"*

---

## Troubleshooting

**Claude asks for permission before every Spotify action**
This is Claude Code's default behaviour. To allow all Spotify tools automatically, add the following to `~/.claude/settings.json`:
```json
"permissions": {
  "allow": ["mcp__spotify__*"]
}
```
Then restart Claude Code.

**The AI says it doesn't have Spotify tools**
Make sure `install.py` completed without errors, then restart the app and try again.

**Setup couldn't register the server with Claude Code**
`install.py` prints the exact command to run at the end of its output — copy and paste it into your terminal. It looks like this (with your own paths):
```
claude mcp add --scope user spotify /path/to/mcp-spotify/.venv/bin/python /path/to/mcp-spotify/server.py
```
On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

If you only use Claude Code through the VS Code extension, the `claude` command isn't available in your terminal. `install.py` finds the copy that comes with the extension automatically; to run commands yourself, install the [Claude Code CLI](https://claude.com/claude-code).

To check that it worked, run `claude mcp get spotify`.

**"Invalid client" or "credentials" error**
Re-run `python3 install.py` and reconfigure when prompted. Check that there are no extra spaces around the Client ID or Secret. Then ask again; there's no need to restart.

**The browser opened but the terminal timed out**
Another program may be using the same port. Try restarting your computer, then re-run `python3 install.py` — it will skip steps already completed and retry the sign-in.

**Queue or playback controls don't work**
These features require Spotify Premium and a device where Spotify is open. If nothing is playing anywhere, open Spotify on your phone or computer, or ask the AI to list your devices and play on one of them.

**The AI says the Spotify login needs renewing**
Spotify logins expire 6 months after you sign in, and new features sometimes need extra permissions. The AI shows the exact command to run; it looks like this, run from the project folder:
```
.venv/bin/python auth.py
```
On Windows, use `.venv\Scripts\python.exe auth.py`. Approve access in the browser, then ask again; there's no need to restart. In the last two weeks before the login expires, this renews it; otherwise it leaves a working login alone. Add `--force` to sign in again anyway.

**Upgrading from an earlier version**
Run `python3 install.py` again. It installs the new dependencies and asks you to approve Spotify's new permissions (for your library, recently played tracks, devices and podcasts). Then restart your AI app.

Earlier versions wrote your Spotify tokens and Client Secret to `mcp-spotify.log` and its rotated copies (`mcp-spotify.log.1` to `.3`), readable by anyone with an account on your computer. The server now makes those files private when it starts, but it's best to delete them. If other people use your computer, also reset the Client Secret in the [Spotify dashboard](https://developer.spotify.com/dashboard), then re-run `python3 install.py` and enter the new one.

**Something else went wrong**
The server writes a log to `mcp-spotify.log` in the project folder. To get more detail, set `MCPOTIFY_LOG_LEVEL=DEBUG` in the server's environment. The log never contains your Spotify tokens.
