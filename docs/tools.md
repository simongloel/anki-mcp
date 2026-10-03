# Tools

Every tool returns JSON. Parameters marked optional can be left out. Wherever a tool takes `deck`, you can pass the deck name (`OTH::Mathematik 1`, case does not matter) or the numeric deck id.

Tools that change something sync before and after the change, and add a `sync` object to the result:

| `sync.status` | Meaning |
|---|---|
| `synced` | the change is on the server |
| `failed` | the change is saved locally and goes out with the next sync; `error` says what happened |
| `not_signed_in` | no valid sign-in; `error` says how to sign in |
| `full_sync_required` | a normal sync is not possible; `error` explains the choice |

Read-only tools sync first when the last sync is older than `ANKI_MCP_READ_SYNC_TTL` seconds (default 60). They only add a `sync` object when that sync did not go through, which means the answer comes from the local copy.

Searches use [Anki's search syntax](https://docs.ankiweb.net/searching.html): `deck:"Mathematik 1"`, `tag:vl03`, `note:Basic`, `is:due`, `is:new`, `is:suspended`, `added:7`, `rated:1`, `prop:lapses>3`, `"front:*Folge*"`, plain words, and `-term` to exclude.

## Account and sync

### `status`

The account, the last sync, whether local changes are waiting, the media sync mode, and the number of notes, cards and decks. Does not contact the server.

### `sync`

| Parameter | | |
|---|---|---|
| `media` | optional | `true` also syncs media files. Default: only if files were added by this server |

### `full_sync`

Replaces one side with the other. Only for when another tool reported `full_sync_required` and the user chose a direction.

| Parameter | | |
|---|---|---|
| `direction` | required | `download` replaces the local copy with the server's collection. `upload` replaces the server's collection; every other device has to download afterwards |
| `confirm` | required | must be `true` |

Refuses an upload when the local collection has no cards, and a download when the server has none.

## Decks

### `list_decks`

| Parameter | | |
|---|---|---|
| `name_contains` | optional | only decks whose name contains this |

Each deck has `id`, `name`, `cards`, `cards_with_subdecks`, `due_new`, `due_learning`, `due_review`.

### `create_deck`

| Parameter | | |
|---|---|---|
| `name` | required | `::` makes subdecks; parents are created too |
| `description` | optional | shown on the deck overview in Anki |

Returns the existing deck when one with that name exists (`created: false`).

### `rename_deck`

| Parameter | | |
|---|---|---|
| `deck` | required | |
| `new_name` | required | a name with `::` moves the deck under another one |

### `delete_deck`

| Parameter | | |
|---|---|---|
| `deck` | required | |
| `delete_cards` | optional | must be `true` if the deck or its subdecks hold cards |

## Note types

### `list_note_types`

| Parameter | | |
|---|---|---|
| `name` | optional | show one note type with its templates and CSS |

Without `name`, every note type with `id`, `name`, `kind` (`standard` or `cloze`), `fields`, `card_types` and `notes`.

### `create_note_type`

| Parameter | | |
|---|---|---|
| `name` | required | |
| `fields` | required | field names in order; the first one identifies a note |
| `templates` | required | list of `{name, front, back}`; fields are referenced as `{{Field}}` |
| `css` | optional | |
| `cloze` | optional | `true` for a cloze type with one template using `{{cloze:Text}}` |

### `update_note_type`

Changes template HTML and CSS. Fields and the set of templates cannot be changed here, because that forces a full sync on every device.

| Parameter | | |
|---|---|---|
| `note_type` | required | name or id |
| `templates` | optional | list of `{name, front, back}`; `front` or `back` may be left out |
| `css` | optional | |

## Notes

### `add_notes`

| Parameter | | |
|---|---|---|
| `deck` | required | created if missing, unless `create_deck` is `false` |
| `notes` | required | list of `{fields, tags}`. `fields` maps field names to content, e.g. `{"Front": "...", "Back": "..."}`. Plain text is fine; line breaks are kept. HTML, `\( \)` and `\[ \]` for MathJax work |
| `note_type` | optional | default `Basic`. `Cloze` has the fields `Text` and `Back Extra` |
| `tags` | optional | added to every note of the call |
| `on_duplicate` | optional | `skip` (default), `update` (overwrite the other fields of the existing note and merge tags), `allow` |
| `create_deck` | optional | default `true` |

A duplicate is a note of the same note type whose first field matches. The result has `added`, `updated`, `skipped_duplicates`, `failed`, `note_ids`, and `problems` with the index and reason of every note that was not added.

### `find_notes`

| Parameter | | |
|---|---|---|
| `query` | required | |
| `limit` | optional | default 50, at most 500 |
| `offset` | optional | for paging |
| `max_field_chars` | optional | default 300; `0` returns fields in full |

Newest first. Each note has `id`, `note_type`, `fields`, `tags`, `decks`, `card_ids`.

### `get_notes`

| Parameter | | |
|---|---|---|
| `note_ids` | required | |

### `update_notes`

| Parameter | | |
|---|---|---|
| `updates` | required | list of `{id, fields, tags, add_tags, remove_tags}`. Only `id` is required. `fields` holds just the fields to change; `tags` replaces all tags |

### `delete_notes`

| Parameter | | |
|---|---|---|
| `note_ids` | required | |

Deletes the notes with all their cards and review history.

## Cards

### `find_cards`

| Parameter | | |
|---|---|---|
| `query` | required | |
| `limit`, `offset` | optional | as in `find_notes` |

Each card has `id`, `note_id`, `deck`, `card_type`, `front`, `state` (`new`, `learning`, `review`, `relearning`), and depending on the state `due`, `new_position`, `interval_days`, `ease_percent`, `reviews`, `lapses`, `suspended`, `buried`, `flag`.

### `move_cards`

| Parameter | | |
|---|---|---|
| `deck` | required | created if missing |
| `card_ids` | optional | |
| `query` | optional | |

At least one of `card_ids` and `query`. Scheduling is kept.

### `suspend_cards`

| Parameter | | |
|---|---|---|
| `card_ids` | optional | |
| `query` | optional | |
| `suspended` | optional | default `true`; `false` unsuspends |

## Tags

### `list_tags`

All tags in the collection.

### `update_tags`

| Parameter | | |
|---|---|---|
| `note_ids` | optional | |
| `query` | optional | |
| `add` | optional | tags to add |
| `remove` | optional | tags to remove |

At least one of `note_ids` and `query`, and at least one of `add` and `remove`. Spaces inside a tag become underscores.

## Media and packages

### `store_media`

| Parameter | | |
|---|---|---|
| `path` | optional | a local file |
| `data_base64` | optional | the file content, if there is no local file |
| `filename` | optional | name to store it under; required with `data_base64` |

Returns `filename` (Anki may rename the file) and `use_in_field`, the `<img src="...">` or `[sound:...]` snippet for a note field.

### `import_package`

| Parameter | | |
|---|---|---|
| `path` | required | an `.apkg` or `.colpkg` file |
| `update_notes` | optional | what to do with notes that already exist: `if_newer` (default), `always`, `never` |
| `with_scheduling` | optional | also import review history and due dates from the package |

Importing the same file twice does not duplicate notes. The result lists `added`, `updated`, `unchanged_duplicates`, `conflicting` and `new_decks`.

### `export_package`

| Parameter | | |
|---|---|---|
| `deck` | required | exported with its subdecks |
| `path` | required | `.apkg` is appended if missing |
| `with_scheduling` | optional | default `false` |
| `with_media` | optional | default `true` |
| `legacy` | optional | the format Anki before 2.1.50 can read |
