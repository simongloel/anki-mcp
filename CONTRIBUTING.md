# Contributing

Issues and pull requests are welcome.

## Before you open a pull request

- Run `uv run pytest` and `uvx ruff check src tests`. The tests start the sync server that ships with the `anki` package and need no AnkiWeb account or network access.
- Add a test for what you changed. `tests/test_ops.py` covers collection edits on a local collection, `tests/test_sync.py` runs two simulated devices against the local sync server, `tests/test_server.py` calls the tools through an MCP client.
- Don't reimplement any part of the sync protocol. Everything that talks to the server goes through `anki.collection.Collection`, so a protocol change stays a version bump of the `anki` dependency.
- Keep edits inside what a normal sync can carry. Anything that changes the collection schema (fields or templates added or removed, note types deleted) forces a full sync on every device of the user and does not belong in a tool.
- Never commit credentials, `state.json`, collections, backups or `.apkg` files from a real account.

## Testing against a real account

Set `ANKI_MCP_DATA_DIR` to a scratch directory and sign in with a test account, not your own. `anki-mcp sync` and `anki-mcp status` are the quickest checks; for the tools, connect the server to an MCP client or write a small client with the [Python SDK](https://github.com/modelcontextprotocol/python-sdk).

## Style

- `ruff` defaults, and comments that say why rather than what.
- Tool descriptions and error messages are read by a model. Write them as plain sentences that say what the tool does, or what went wrong and what to do next.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(notes): ...`, `fix(sync): ...`, `docs: ...`.
