# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-03

### Added

- Sign-in with an AnkiWeb account or a self-hosted Anki sync server. Credentials come from the environment or from `anki-mcp login`; only the session key is stored.
- Sync before and after every change through Anki's own backend (the `anki` package), including the AnkiWeb host redirect, re-login when the session key expires, and the safe full-sync cases (empty local collection, nothing unsynced) handled on their own. Anything that would lose data needs `full_sync` with confirmation.
- Note tools: `add_notes` (bulk, duplicate handling, deck created on the fly), `find_notes`, `get_notes`, `update_notes`, `delete_notes`.
- Deck tools: `list_decks` with due counts, `create_deck`, `rename_deck`, `delete_deck`.
- Card tools: `find_cards` with scheduling details, `move_cards`, `suspend_cards`.
- Tag tools: `list_tags`, `update_tags`.
- Note type tools: `list_note_types`, `create_note_type`, `update_note_type` (templates and CSS only, so no full sync is forced).
- `store_media`, `import_package`, `export_package`.
- `status`, `sync`, `full_sync`, and the `anki-mcp login`, `logout`, `status` and `sync` commands.
- Backups through Anki's backup routine before edits, a lock file so several server processes can share one collection, and archiving of the old collection when the account changes.
- Tests that run against the sync server shipped with the `anki` package.

[Unreleased]: https://github.com/simongloel/anki-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/simongloel/anki-mcp/releases/tag/v0.1.0
