# Spotify MCP

Control Spotify with plain English through your AI assistant. Search for music, build playlists, manage your queue, and control playback — just by asking.

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
- *"Remove the last 5 tracks I added to my study playlist"*

### Queue
- *"Queue 10 tracks that match the vibe of what I'm listening to"*
- *"Find some dreamy shoegaze songs and add them to my queue"*
- *"Queue the full discography of Tame Impala"*

### Playback
- *"Pause"* / *"Resume"* / *"Skip this"* / *"Go back"*

### Discovery & browsing
- *"Search for melancholic jazz from the 60s"*
- *"Show me all albums by Nick Cave"*
- *"What are my most listened-to artists this month?"*
- *"What am I listening to right now?"*

---

## Compatible AI apps

| App | Supported | Notes |
|---|---|---|
| Claude Code | Yes | Automatic setup via `setup.py` |
| ChatGPT desktop app | Yes | Manual setup required — see below |
| Cursor | Yes | Manual setup required — see below |
| Windsurf | Yes | Manual setup required — see below |
| Claude.ai (browser) | No | Browser-based Claude does not support MCP |

> The quality of results depends on the AI model. Models that are better at following multi-step instructions will tend to produce better results.

---

## What you need

- A **Spotify account** (free or premium — note: queue and playback control require Spotify Premium)
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
python3 setup.py
```

**Windows:**
```
cd %USERPROFILE%\Desktop\mcp-spotify
python3 setup.py
```

> If you unzipped the project somewhere other than your Desktop, replace `Desktop/mcp-spotify` with the actual path to your folder, e.g. `Documents/mcp-spotify`.

The script will:
- Ask for your Client ID and Client Secret from Step 2
- Install everything automatically
- Open a browser to log in to Spotify (approve access and wait for the confirmation page)
- Register the server with Claude Code automatically

That's it — you're ready to go.

---

## Connecting to other AI apps

> **Not sure which to choose?** Claude Code has automatic setup and is the easiest option. If you're comfortable with editing config files, you can use any of the apps below.

If you use ChatGPT, Cursor, or Windsurf instead of Claude Code, you'll need to register the server manually after running `setup.py`. You'll need to know the full path to the project folder on your computer.

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

## Try it out

Open your AI app and try one of these to get started:

> *"What am I currently listening to on Spotify?"*
> *"Search for some late night lo-fi tracks and make a playlist called Late Night"*
> *"What are my top artists from the last month?"*

---

## Troubleshooting

**The AI says it doesn't have Spotify tools**
Make sure `setup.py` completed without errors, then restart the app and try again.

**"Invalid client" or "credentials" error**
Re-run `python3 setup.py` and reconfigure when prompted. Check that there are no extra spaces around the Client ID or Secret.

**The browser opened but the terminal timed out**
Another program may be using the same port. Try restarting your computer, then re-run `python3 setup.py` — it will skip steps already completed and retry the auth.

**Queue or playback controls don't work**
These features require Spotify Premium.

**Need to re-authenticate**
Delete the `.cache` file in the project folder and run `python3 setup.py` again.
