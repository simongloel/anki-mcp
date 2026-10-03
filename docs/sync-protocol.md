# How Anki syncs with AnkiWeb

Notes from reading the sync code in [ankitects/anki](https://github.com/ankitects/anki) at version 26.09.3 (commit `7a4db003`). Paths below are relative to that repository. anki-mcp does not implement any of this itself; it calls the same code through the `anki` Python package. The notes are here so the behaviour of the server can be explained.

## Transport

Everything is an HTTPS POST to `https://sync.ankiweb.net/`, or to a self-hosted server. Collection methods live under `sync/`, media methods under `msync/` (`rslib/src/sync/collection/protocol.rs`, `rslib/src/sync/media/protocol.rs`).

The current protocol version is 11, in use since January 2023 (`rslib/src/sync/version.rs`). Versions 8 to 10 packed requests into gzip multipart forms and are only kept for old clients.

A version 11 request has:

- a header `anki-sync` holding JSON: `{"v": 11, "k": "<host key>", "c": "<client version>", "s": "<session key>"}`
- a zstd-compressed body with content type `application/octet-stream`. For most methods the uncompressed body is JSON, and a method without arguments sends `{}`

`k` is empty for the login call. `s` is a short random string the client makes up per sync, so the server can tell parallel sync attempts apart. `c` looks like `26.09.3,<build hash>,linux`.

Responses are zstd-compressed too and carry the uncompressed length in an `anki-original-size` header (`rslib/src/sync/request/header_and_stream.rs`, `rslib/src/sync/response.rs`).

The payload limit is 100 MB compressed and 300 MB uncompressed. The client checks the limit itself when the endpoint is AnkiWeb (`rslib/src/sync/request/mod.rs`).

## Login

`POST sync/hostKey` with `{"u": "<email>", "p": "<password>"}` returns `{"key": "<host key>"}`. HTTP 403 means wrong email or password (`rslib/src/sync/login.rs`).

The host key is the credential for everything after that. Anki desktop stores it in the profile and forgets the password, and anki-mcp does the same in `state.json`. If AnkiWeb stops accepting a key, later calls fail with an auth error and the client has to log in again.

## Deciding what kind of sync is needed

`POST sync/meta` with `{"v": 11, "cv": "anki,26.09.3 (<hash>),linux"}` returns the state of the collection on the server (`rslib/src/sync/collection/meta.rs`):

| Field | Meaning |
|---|---|
| `mod` | time of the last change, in milliseconds |
| `scm` | time of the last schema change, in milliseconds |
| `usn` | update sequence number of the server |
| `ts` | server clock, in seconds |
| `msg` | a message to show the user |
| `cont` | false if the server refuses to sync; `msg` says why |
| `empty` | true if the collection has no cards |
| `media_usn` | sequence number of the media store |

AnkiWeb spreads accounts over several hosts. If the account lives elsewhere, `meta` answers with HTTP 308 and the new base URL, which the client stores and uses from then on.

The client compares that to its own collection:

- `mod` is equal: nothing to do.
- `scm` differs: the two collections have no common base, and one has to replace the other. Uploading is offered unless the local side is empty and the remote is not; downloading is offered unless the remote is empty and the local side is not.
- otherwise: normal sync.

If the clocks differ by more than 300 seconds the client refuses to sync (`rslib/src/sync/collection/status.rs`).

The schema time changes when something happens that the normal sync cannot express as a per-object change: fields or templates of a note type added, removed or reordered, a note type or an options preset deleted, notes switched to another note type, a collection restored from a backup file, "Check Database" repairing something.

## Normal sync

Every object carries a `usn`. Objects changed locally since the last sync have `usn = -1`. The sync exchanges everything newer than the last common `usn` in both directions, and for a card or note changed on both sides the one with the later modification time wins (`rslib/src/sync/collection/chunks.rs`). The steps (`rslib/src/sync/collection/normal.rs`):

1. `start` with `{"minUsn": <usn at last sync>, "lnewer": <local mod is later>}`. The server answers with its "graves": ids of cards, notes and decks deleted there.
2. `applyGraves`, repeated: the client sends its own deletions in chunks of 250 ids, then applies the server's.
3. `applyChanges`: note types, decks, deck options and tags go over in one request, and the server answers with its own changes of the same kinds. The collection config and creation time are only sent if the local side is newer.
4. `chunk`, repeated until the server says `done`: review log entries, cards and notes from the server, at most 250 rows per chunk.
5. `applyChunk`, repeated: the same from the client to the server.
6. `sanityCheck2`: the client sends its counts of cards, notes, review log entries, graves, note types, decks and deck options, plus the due counts. The server compares them with its own.
7. `finish`: the server returns the new modification time, and both sides store it together with the new `usn`.

The client wraps all of this in one database transaction. On any error it rolls back and calls `abort`. If the sanity check fails, it also marks its schema as changed, which forces a full sync next time. That is Anki's way of recovering from a collection that no longer adds up.

## Full sync

`upload` sends the whole collection, the SQLite file in schema version 18, as the request body. The server checks it and replaces its copy. `download` returns the server's file and the client replaces its own (`rslib/src/sync/collection/upload.rs`, `download.rs`).

After an upload every other device sees a different `scm` at its next sync and has to download. Reviews that such a device had not synced yet are lost, which is why Anki asks before a full sync, and why anki-mcp only downloads on its own when the local copy has no unsynced changes.

## Media

Media files sync separately, with their own sequence numbers, through `msync/begin`, `mediaChanges`, `downloadFiles`, `uploadChanges` and `mediaSanity` (`rslib/src/sync/media/`). The client first fetches the list of changes since its last media `usn` and downloads the files it lacks as zip archives, then uploads its own additions and deletions as zips, and at the end both sides compare file counts.

A device that has never synced media gets the whole library on the first run. That is the reason anki-mcp leaves media alone until it has a file of its own to upload.

## The local side

Anki's backend exposes sync to the Python layer as a handful of calls (`proto/anki/sync.proto`, `rslib/src/backend/sync.rs`, `pylib/anki/collection.py`):

| Call | What it does |
|---|---|
| `sync_login(username, password, endpoint)` | `hostKey`; returns the auth with the host key |
| `sync_collection(auth, sync_media)` | `meta`, then a normal sync if one is possible. The result says `NO_CHANGES` when it is done, or `FULL_SYNC`, `FULL_DOWNLOAD`, `FULL_UPLOAD` when a one-way sync is needed. It also returns the new endpoint after a redirect |
| `full_upload_or_download(auth, server_usn, upload)` | replaces one side with the other |
| `sync_media(auth)`, `media_sync_status()` | starts the media sync on a background thread and reports on it |

`anki_mcp/session.py` is a small loop around these four: get an auth, call `sync_collection`, decide what to do with a full-sync answer, and wait for the media sync when there is one.

## Testing without AnkiWeb

The same package contains the sync server (`python -m anki.syncserver`, `rslib/src/sync/http_server/`). It reads accounts from `SYNC_USER1=name:password` and stores collections under `SYNC_BASE`. The test suite starts one per test and runs real syncs between several local collections, so the protocol path is covered without touching AnkiWeb.
