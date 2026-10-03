# Security

anki-mcp handles your AnkiWeb password and a full copy of your collection. Here is what it does with them.

- The password is only sent to the sync server, `https://sync.ankiweb.net/` (or the host AnkiWeb redirects to, or the `ANKI_SYNC_ENDPOINT` you configured), in the login request Anki desktop also sends. The server never writes it to disk. If you put it into the `env` block of your MCP config, that file contains it in plain text; that is your choice. `anki-mcp login` avoids that.
- The session key the sync server returns is stored in `state.json` in the data directory, readable only by your user (mode 0600 on macOS and Linux; on Windows it inherits the permissions of your profile folder). Whoever has that key can sync your collection. `anki-mcp logout` deletes it.
- The collection, its media and backups are plain SQLite and files in the data directory, unencrypted, the same way Anki desktop stores them.
- The model never sees the password. It sees field contents, tags, deck names and search results, which go to the model provider as part of your chat.
- The server listens on nothing. It talks to the client over stdin and stdout and to the sync server over HTTPS.

## Reporting a problem

If you find a way to get at credentials, the session key or collection data through the server, please don't open a public issue. Use GitHub's private vulnerability reporting on this repository instead.
