# Installation

This guide sets up anki-mcp with the common MCP clients. It takes about five minutes. You need an AnkiWeb account, or the address of a self-hosted Anki sync server.

## 1. Install the server

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it. Then:

```bash
uv tool install git+https://github.com/simongloel/anki-mcp
```

This puts `anki-mcp` on your PATH (`~/.local/bin` on macOS and Linux, `%USERPROFILE%\.local\bin` on Windows; uv tells you if that directory isn't on the PATH yet and `uv tool update-shell` fixes it). The Anki library it depends on comes as a prebuilt wheel for Windows, macOS and Linux, so nothing has to be compiled.

To update later:

```bash
uv tool upgrade anki-mcp
```

## 2. Sign in

Pick one of the two ways.

### Option A: sign in once

```bash
anki-mcp login
```

It asks for your AnkiWeb email and password, signs in, and runs the first sync, which downloads your collection. The password is not kept; the session key AnkiWeb returns is stored in the data directory, readable only by you. From then on the server needs nothing else. If AnkiWeb ever stops accepting the key, run `anki-mcp login` again.

### Option B: login in the environment

Set `ANKIWEB_USERNAME` and `ANKIWEB_PASSWORD` where the server runs, usually in the `env` block of your client's MCP config (see below). The server signs in on its own, and signs in again whenever the session key expires. The downside is that the config file then holds your password in plain text.

If you want the password out of the config but still automatic, put it in a file only you can read and start the server through a small script that loads the file:

```sh
#!/bin/sh
set -a
. "$HOME/.config/anki-mcp.env"   # ANKIWEB_USERNAME=... and ANKIWEB_PASSWORD='...'
set +a
exec anki-mcp "$@"
```

### Check

```bash
anki-mcp status
anki-mcp sync
```

`status` shows the account, the last sync and the counts without touching the network. `sync` runs a sync and prints what it did.

If your AnkiWeb account has no cards yet, the first sync has to upload the local (empty) collection in one piece. The server won't do that on its own; ask your assistant, and it calls `full_sync` with `direction='upload'` after you confirm.

## 3. Connect your client

Replace `anki-mcp` with the full path if your client doesn't see your PATH. `which anki-mcp` (macOS, Linux) or `where anki-mcp` (Windows) prints it.

### Claude Code

```bash
claude mcp add -s user anki -- anki-mcp serve
```

With the login in the config:

```bash
claude mcp add -s user anki \
  --env ANKIWEB_USERNAME=you@example.com \
  --env ANKIWEB_PASSWORD='your password' \
  -- anki-mcp serve
```

`-s user` makes it available in every project. Restart Claude Code afterwards and check with `/mcp`.

### Claude Desktop

Settings, Developer, Edit Config. Add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "anki": {
      "command": "anki-mcp",
      "args": ["serve"],
      "env": {
        "ANKIWEB_USERNAME": "you@example.com",
        "ANKIWEB_PASSWORD": "your password"
      }
    }
  }
}
```

Leave the `env` block out if you used `anki-mcp login`. Claude Desktop does not read your shell's PATH, so use the full path to `anki-mcp` here.

### Cursor

Settings, MCP, Add new global MCP server. The config has the same shape as Claude Desktop's.

### VS Code (Copilot)

Add to `.vscode/mcp.json` in a workspace or to your user settings:

```json
{
  "servers": {
    "anki": {
      "type": "stdio",
      "command": "anki-mcp",
      "args": ["serve"]
    }
  }
}
```

## Self-hosted sync server

If you run your own [Anki sync server](https://docs.ankiweb.net/sync-server.html), set `ANKI_SYNC_ENDPOINT` to its base URL, for example `http://192.168.1.10:8080/`, next to the username and password of a user defined on that server. Everything else stays the same.

## Where the data lives

| | |
|---|---|
| Linux | `~/.local/share/anki-mcp` (or `$XDG_DATA_HOME/anki-mcp`) |
| macOS | `~/.local/share/anki-mcp` |
| Windows | `%USERPROFILE%\.local\share\anki-mcp` |

Set `ANKI_MCP_DATA_DIR` to put it somewhere else. The directory holds the collection, its media, backups, the lock file and `state.json` with the session key. To start over, sign out with `anki-mcp logout` and delete the directory.

## Troubleshooting

**"rejected the username or password".** Check that you can sign in on [ankiweb.net](https://ankiweb.net). AnkiWeb accounts use the email address, not a username.

**"Another anki-mcp process is using the collection".** Two servers share the data directory and one of them is in the middle of a sync. The waiting one gives up after 60 seconds (`ANKI_MCP_LOCK_TIMEOUT`). Usually the second attempt works.

**A tool says a full sync is required.** The local collection and the server have diverged in a way Anki cannot merge, for example because a note type gained a field on another device. Your assistant explains the two directions; read [the README](../README.md#full-sync) before you pick one.

**The first media sync takes long.** It downloads the whole media library of your account. It only starts once the server has a media file of its own to upload, unless `ANKI_MCP_SYNC_MEDIA=always`.
