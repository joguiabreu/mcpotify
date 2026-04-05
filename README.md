# Spotify MCP

This connects your AI assistant to your Spotify account via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). Once set up, you can ask your AI things like:

- *"Make me a relaxing playlist for studying"*
- *"Find 20 upbeat songs similar to Daft Punk and create a playlist called Friday Night"*
- *"Queue 10 songs that match the vibe of what I'm currently listening to"*
- *"What are my top artists this month? Queue something in that style."*

The AI will search Spotify, pick tracks, and create playlists or queue songs directly in your account.

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
   - **Redirect URI:** paste this exactly: `http://127.0.0.1:8888/callback`
   - Check the **Web API** checkbox
4. Click **Save**.
5. On the next page, click **Settings** (top right).
6. You'll see a **Client ID** — copy it and keep the page open.
7. Click **View client secret** — copy that too.

### Step 3 — Run the setup script

Open a **Terminal** (Mac/Linux) or **Command Prompt** (Windows), navigate to the project folder, and run:

```
python3 setup.py
```

The script will:
- Ask for your Client ID and Client Secret from Step 2
- Create a virtual environment and install dependencies
- Open a browser to log in to Spotify (approve access and wait for confirmation)
- Register the MCP server with Claude Code automatically

That's it.

### Connecting other AI apps

If you use ChatGPT, Cursor, or Windsurf instead of Claude Code, register the server manually after running `setup.py`:

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
> *"Queue 10 songs that match the vibe of what I'm listening to right now"*
> *"What are my top artists this month? Queue something similar but more upbeat."*

---

## Troubleshooting

**The AI says it doesn't have Spotify tools**
- Make sure `setup.py` completed without errors.
- Restart the app and try again.

**"Invalid client" or "credentials" error**
- Re-run `python3 setup.py` and choose to reconfigure when prompted. Check that there are no extra spaces around the values.

**The browser opened but the terminal timed out**
- Make sure port 8888 is not blocked or in use by another process. Re-run `python3 setup.py` — it will skip steps already completed and retry the auth.

**Need to re-authenticate**
- Delete `.cache` and run `python3 setup.py` again. All other steps will be skipped automatically.
