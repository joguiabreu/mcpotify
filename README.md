# Spotify MCP

This connects your AI assistant to your Spotify account via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). Once set up, you can ask your AI things like:

- *"Make me a relaxing playlist for studying"*
- *"Find 20 upbeat songs similar to Daft Punk and create a playlist called Friday Night"*
- *"Recommend tracks like Radiohead but more acoustic"*

The AI will search Spotify, pick tracks, and create the playlist directly in your account.

---

## Compatible AI apps

This server uses the MCP standard, which is supported by several AI apps. The steps to connect it vary depending on which one you use:

| App | Supported | How to connect |
|---|---|---|
| Claude Code | Yes | See Step 6 below |
| ChatGPT desktop app | Yes | Settings → MCP Servers → add server |
| Cursor | Yes | `.cursor/mcp.json` config file |
| Windsurf | Yes | MCP settings panel |
| OpenAI API (direct) | No | Not supported natively |

> **Note:** All apps that support MCP will work with this server — but the quality of results depends on the model. Models that are better at following multi-step instructions will tend to produce better playlists.

---

## What you need before starting

- A **Spotify account** (free or premium)
- **Python 3.10 or later** — [download here](https://www.python.org/downloads/). During install on Windows, check the box that says *"Add Python to PATH"*.
- One of the compatible AI apps listed above

---

## Setup (do this once)

### Step 1 — Get the files

Download this project and unzip it somewhere on your computer (e.g. your Desktop or Documents folder).

### Step 2 — Create a Spotify app

You need to tell Spotify that a program is allowed to access your account. This is free and takes about 2 minutes.

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and log in with your Spotify account.
2. Click **Create app**.
3. Fill in the form:
   - **App name:** anything you like, e.g. `My Spotify MCP`
   - **App description:** anything, e.g. `Personal use`
   - **Redirect URI:** paste this exactly: `https://oauth.pstmn.io/v1/callback`
   - Check the **Web API** checkbox
4. Click **Save**.
5. On the next page, click **Settings** (top right).
6. You'll see a **Client ID** — copy it and keep the page open.
7. Click **View client secret** — copy that too.

### Step 3 — Add your credentials

Inside the project folder, create a file called `.env` (exactly that name, with the dot). Open it with any text editor (Notepad on Windows, TextEdit on Mac) and paste this, replacing the placeholders with your values from Step 2:

```
SPOTIFY_CLIENT_ID=paste_your_client_id_here
SPOTIFY_CLIENT_SECRET=paste_your_client_secret_here
SPOTIFY_REDIRECT_URI=https://oauth.pstmn.io/v1/callback
```

Save the file.

### Step 4 — Install dependencies

Open a **Terminal** (Mac) or **Command Prompt** (Windows) and run these commands one at a time. Replace `/path/to/mcp-spotify` with the actual path to the folder you unzipped.

```
cd /path/to/mcp-spotify
python3 -m venv .venv
```

On **Mac/Linux:**
```
source .venv/bin/activate
pip install -r requirements.txt
```

On **Windows:**
```
.venv\Scripts\activate
pip install -r requirements.txt
```

### Step 5 — Log in to Spotify (first time only)

Still in the terminal, run:

```
python3 auth.py
```

A browser window will open asking you to log in to Spotify and approve access. After you approve it, you'll be redirected to a page that may look broken — that's fine. Copy the full URL from your browser's address bar, paste it back into the terminal, and press Enter.

This only happens once. After that, your AI assistant handles everything automatically.

### Step 6 — Connect to your AI app

**Claude Code:**
```
claude mcp add --scope user spotify /path/to/mcp-spotify/.venv/bin/python /path/to/mcp-spotify/server.py
```
On Windows, replace `.venv/bin/python` with `.venv/Scripts/python`.

**ChatGPT desktop app:**
Go to Settings → MCP Servers and add a new server with:
- Command: `/path/to/mcp-spotify/.venv/bin/python`
- Arguments: `/path/to/mcp-spotify/server.py`

**Cursor / Windsurf:**
Add the following to your MCP config file (`.cursor/mcp.json` for Cursor):
```json
{
  "mcpServers": {
    "spotify": {
      "command": "/path/to/mcp-spotify/.venv/bin/python",
      "args": ["/path/to/mcp-spotify/server.py"]
    }
  }
}
```

---

## You're done!

Open your AI app and try asking:

> *"Search for some dreamy shoegaze tracks and make a playlist called Dream Pop"*

---

## Troubleshooting

**The AI says it doesn't have Spotify tools**
- Make sure you completed Step 6 for your specific app.
- Restart the app and try again.

**"Invalid client" or "credentials" error**
- Double-check your `.env` file. Make sure there are no extra spaces around the `=` signs.

**The browser opened but nothing happened after I approved**
- Copy the full URL from the browser address bar (even if the page looks broken) and paste it into the terminal.
