# anki-mcp

An MCP server that lets an AI assistant work on your Anki collection and sync it with your AnkiWeb account. Cards it creates are in Anki desktop, AnkiDroid and AnkiMobile after their next sync. No .apkg files to import by hand, and Anki does not have to be running.

You can then ask things like:

- "Make 30 cards from this chapter and put them in OTH::Mathematik 1::VL03."
- "The card about Cauchy sequences has a typo, fix it."
- "Which cards in Programmieren 1 have I failed more than three times?"
- "Move everything tagged vl05 into its own subdeck."
- "Import the deck oth-mcp just exported."

[docs/tools.md](docs/tools.md) has every tool and parameter, [docs/installation.md](docs/installation.md) walks through the setup per client.

## How it works

Most Anki integrations talk to AnkiConnect, an add-on inside a running Anki desktop. This one doesn't need the desktop app. It keeps its own copy of your collection and syncs it with AnkiWeb, so to AnkiWeb it is one more device next to your laptop and your phone.

The sync is not a reimplementation. anki-mcp loads [`anki`](https://pypi.org/project/anki/), the library Anki desktop itself is built on, and calls the same Rust code for login, sync and every edit. [docs/sync-protocol.md](docs/sync-protocol.md) describes what happens on the wire. The merge rules behind it are long and unforgiving, and a second implementation that gets one of them wrong corrupts a collection. With Anki's own code, a protocol change is a dependency bump.

Each tool call that changes something does this:

1. Open the local collection and sync, so the edit starts from what is on AnkiWeb right now.
2. Make the change.
3. Sync again and report whether the change reached the server.

If AnkiWeb can't be reached before the edit, the edit is refused. If the sync after the edit fails, the change stays local and goes out with the next sync; the result says so.

## Tools

| Tool | What it does |
|---|---|
| `add_notes` | Add any number of notes to a deck in one call. Creates the deck if needed, skips or updates duplicates |
| `find_notes`, `get_notes` | Search with Anki's browser syntax, read fields, tags and decks |
| `update_notes` | Change fields and tags of existing notes |
| `delete_notes` | Delete notes with their cards |
| `list_decks`, `create_deck`, `rename_deck`, `delete_deck` | Manage decks and subdecks, with card counts and what is due |
| `find_cards` | Scheduling details: state, due date, interval, ease, lapses |
| `move_cards`, `suspend_cards` | Move cards between decks, suspend and unsuspend |
| `list_tags`, `update_tags` | Tag or untag many notes at once |
| `list_note_types`, `create_note_type`, `update_note_type` | Fields, card templates and CSS |
| `store_media` | Add an image or audio file and get the snippet for a note field |
| `import_package`, `export_package` | Read and write .apkg files |
| `status`, `sync`, `full_sync` | Sign-in state, manual sync, one-way sync |

## Install

You need [uv](https://docs.astral.sh/uv/); it fetches Python 3.12 if you don't have it. Then:

```bash
uv tool install git+https://github.com/simongloel/anki-mcp
```

That puts `anki-mcp` on your PATH. The Anki library comes as a prebuilt wheel for Windows, macOS and Linux. For development, clone the repository and run `uv sync` instead; the server is then at `.venv/bin/anki-mcp`.

### Sign in

There are two ways. Pick one.

Sign in once in a terminal. The password is sent to AnkiWeb and then forgotten; only the session key AnkiWeb returns is stored, which is what Anki desktop does too:

```bash
anki-mcp login
```

Or put the login in the environment of the server. It then signs in on its own, and again if AnkiWeb ever invalidates the session key:

```bash
ANKIWEB_USERNAME=you@example.com
ANKIWEB_PASSWORD='your password'
```

The first sync downloads your whole collection. `anki-mcp status` shows what the server knows without touching the network, `anki-mcp sync` runs a sync and prints the result.

### Add it to your client

For Claude Code, after `anki-mcp login`:

```bash
claude mcp add -s user anki -- anki-mcp serve
```

With the login in the config instead:

```bash
claude mcp add -s user anki \
  --env ANKIWEB_USERNAME=you@example.com \
  --env ANKIWEB_PASSWORD='your password' \
  -- anki-mcp serve
```

For Claude Desktop, Cursor and other clients (use the full path to `anki-mcp` if the client doesn't see your PATH):

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

[docs/installation.md](docs/installation.md) has the details per client, and how to keep the password out of the config file but still sign in automatically.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `ANKIWEB_USERNAME`, `ANKIWEB_PASSWORD` | none | AnkiWeb login. Not needed after `anki-mcp login` |
| `ANKI_SYNC_ENDPOINT` | AnkiWeb | Base URL of a self-hosted Anki sync server |
| `ANKI_MCP_DATA_DIR` | `~/.local/share/anki-mcp` | Where the collection, its media, backups and the session key live |
| `ANKI_MCP_SYNC_MEDIA` | `auto` | `auto` syncs media only after this server added a file, `always` with every edit, `never` not at all |
| `ANKI_MCP_READ_SYNC_TTL` | `60` | Read-only tools skip the sync if the last one is younger than this many seconds |
| `ANKI_MCP_MEDIA_TIMEOUT` | `300` | Seconds to wait for a media sync before leaving the rest for next time |

## Things to know

### AnkiWeb's terms

[AnkiWeb's terms](https://ankiweb.net/account/terms) say that AnkiWeb "does not currently allow access from browser extensions or other third-party clients" and point to AnkiConnect instead. anki-mcp uses Anki's own library and behaves like one more Anki device, but it is still software the Anki team did not publish. Using it with AnkiWeb is therefore against those terms as written, and the Anki team could block an account for it. The free service is paid for by AnkiMobile sales; be a good guest, keep the number of syncs reasonable, and consider buying AnkiMobile if you use AnkiWeb a lot.

The setup that is in the clear is a [self-hosted Anki sync server](https://docs.ankiweb.net/sync-server.html), which the Anki team publishes for exactly this kind of use. Point `ANKI_SYNC_ENDPOINT` at it and the server never talks to AnkiWeb. If you need AnkiWeb and want to stay within its terms, run Anki desktop with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on and use an MCP server built on that, such as [anki-mcp-server](https://github.com/ankimcp/anki-mcp-server); Anki itself then does the syncing.

### Full sync

Anki merges most changes from several devices. It cannot merge some: a field added to a note type, a collection restored from a backup, or the very first upload to an empty account. Then one side has to replace the other, and Anki desktop asks you which.

anki-mcp handles the safe case alone. If AnkiWeb requires a full sync and the local copy has nothing unsynced, it downloads. Whenever local changes would be lost, or AnkiWeb would be overwritten, it stops and the assistant has to ask you before calling `full_sync`. An upload replaces the collection on AnkiWeb, and other devices lose reviews they had not synced yet.

For the same reason `update_note_type` only changes template HTML and CSS. Adding or removing fields forces a full sync on every device, so do that in Anki.

### Media

Images and audio sync separately from cards. The first media sync downloads the media library of your account, which can be large, so by default it only happens once you add a file with `store_media` or import a package that contains media. Set `ANKI_MCP_SYNC_MEDIA=never` if you only ever create text cards and want to rule it out.

### Your data

The collection is a normal Anki collection in `ANKI_MCP_DATA_DIR`. Before edits, Anki's own backup routine writes a backup to `backups/` there, at most one every 30 minutes, and it keeps the same number of backups Anki desktop would. A backup is also written before a full download replaces a local copy that has cards.

Several server processes can share the data directory, for example two Claude sessions. They take turns through a lock file.

If you switch to another account or sync server, the old collection is moved to `archive/` in the data directory instead of being merged into the new account.

### With oth-mcp

[oth-mcp](https://github.com/simongloel/oth-mcp) reads lecture material and writes cards to an .apkg file. With both servers connected, the assistant can either pass that file to `import_package` or skip the file and call `add_notes` directly. Importing the same deck twice updates cards instead of duplicating them.

## Development

```bash
uv sync
uv run pytest
```

The tests start Anki's own sync server (it ships inside the `anki` package) and run real logins, syncs between two simulated devices, conflicts, media syncs and imports against it. Nothing in the test suite talks to AnkiWeb. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

AGPL-3.0-or-later, the license of the Anki library this server links against. anki-mcp is not affiliated with or endorsed by Anki, AnkiWeb or Ankitects Pty Ltd.
