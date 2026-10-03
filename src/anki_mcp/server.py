"""The MCP tools."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Annotated, Any, Literal

import anyio
from anki.collection import Collection
from anki.errors import AnkiException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__, ops
from .config import Config
from .session import AnkiMcpError, Session

INSTRUCTIONS = """\
anki-mcp edits the user's Anki collection and syncs it with their AnkiWeb account (or their own \
sync server), so new cards show up in Anki desktop, AnkiDroid and AnkiMobile after their next sync.

It works on its own copy of the collection, like one more Anki device. Every tool that changes \
something syncs first, makes the change and syncs again; the "sync" entry in the result says \
whether the change reached the server. You do not need to call sync yourself.

Typical flows:
- "Make cards for X in deck Y" -> add_notes with all cards in one call. The deck is created if \
it is missing. Use note_type "Basic" (fields Front, Back) or "Cloze" (fields Text, Back Extra) \
unless the user has their own; list_note_types shows the fields.
- Another tool already wrote an .apkg file (for example anki_create_deck of oth-mcp) -> \
import_package with the file path puts it straight into the account.
- "Fix / reword / retag this card" -> find_notes, then update_notes.
- "Delete ..." -> find_notes or find_cards first, show the user what would go, then delete_notes.

Vocabulary: a deck (German: Stapel) holds cards. A note is the content the user edits (its \
fields and tags); Anki generates one or more cards from each note. Decks nest with "::", \
e.g. "OTH::Mathematik 1::VL03".

Field values are HTML. Plain text is fine, line breaks are kept. Use <b>, <ul><li>, <code> for \
structure, \\( ... \\) for inline MathJax and \\[ ... \\] for display math.

Searches use Anki's browser syntax: deck:"Name", tag:name, note:"Basic", is:due, is:new, \
is:suspended, added:7, "front:*word*", plain words, and -term to exclude.

If a result says a full sync is required, explain the choice to the user before calling \
full_sync; a full sync replaces one side with the other. Never ask the user for their AnkiWeb \
password in the chat. If the server is not signed in, tell them to set ANKIWEB_USERNAME and \
ANKIWEB_PASSWORD in the server's environment or to run `anki-mcp login` in a terminal.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

Deck = Annotated[str, Field(description="Deck name, with :: for subdecks, or a deck id")]
CardIds = Annotated[list[int] | None, Field(description="Card ids, e.g. from find_cards")]
Query = Annotated[str | None, Field(description="Anki search, e.g. deck:\"Mathe 1\" tag:vl03")]


class NoteInput(BaseModel):
    fields: dict[str, str] = Field(
        description='Field name to content, e.g. {"Front": "...", "Back": "..."} for Basic or '
        '{"Text": "{{c1::Paris}} is the capital of France"} for Cloze. Fields left out stay empty.'
    )
    tags: list[str] = Field(default_factory=list, description="Tags for this note")


class NoteUpdate(BaseModel):
    id: int = Field(description="Note id")
    fields: dict[str, str] | None = Field(
        default=None, description="Only the fields to change; the others keep their content"
    )
    tags: list[str] | None = Field(default=None, description="Replaces all tags of the note")
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None


class Template(BaseModel):
    name: str | None = Field(default=None, description="Card type name, e.g. 'Card 1'")
    front: str = Field(description="Front template, e.g. '{{Front}}'")
    back: str = Field(description="Back template, e.g. '{{FrontSide}}<hr id=answer>{{Back}}'")


class TemplateUpdate(BaseModel):
    name: str = Field(description="Name of the existing card type to change")
    front: str | None = None
    back: str | None = None


def build_server(cfg: Config) -> MCPServer:
    session = Session(cfg)
    server = MCPServer("anki", title="Anki", instructions=INSTRUCTIONS, version=__version__)

    async def call(fn: Callable[[], Any]) -> Any:
        try:
            return await anyio.to_thread.run_sync(fn)
        except (AnkiMcpError, AnkiException) as e:
            raise ToolError(str(e)) from e

    async def read(fn: Callable[[Collection], dict[str, Any]]) -> dict[str, Any]:
        result, sync = await call(partial(session.read, fn))
        if sync["status"] not in ("fresh", "synced"):
            # The answer comes from the local copy and may be out of date.
            result["sync"] = sync
        return result

    async def write(fn: Callable[[Collection], dict[str, Any]], adds_media: bool = False) -> dict[str, Any]:
        result, sync = await call(partial(session.write, fn, adds_media=adds_media))
        result["sync"] = sync
        return result

    # Account and sync
    ######################################################################

    @server.tool(
        title="Sign-in and sync status",
        description="Shows which account the server uses, when it last synced, whether local "
        "changes are waiting to be synced, and how many notes, cards and decks there are. "
        "Does not contact the server.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def status() -> dict[str, Any]:
        return await call(session.status)

    @server.tool(
        title="Sync now",
        description="Syncs the collection with AnkiWeb in both directions. Edits sync on their "
        "own; call this to pick up what the user just changed on another device, or to retry "
        "after a failed sync. media=true also syncs images and audio, which downloads the "
        "account's whole media library the first time.",
        annotations=WRITE,
    )
    async def sync(
        media: Annotated[
            bool | None, Field(description="Sync media files too. Default: only if files were added here.")
        ] = None,
    ) -> dict[str, Any]:
        return await call(partial(session.sync, media=media))

    @server.tool(
        title="One-way full sync",
        description="Replaces one side with the other when a normal sync is not possible. "
        "'download' overwrites the local copy with AnkiWeb's collection and drops local changes "
        "that never synced. 'upload' overwrites the collection on AnkiWeb, and every other device "
        "has to download it and loses reviews it had not synced. Only call this after another "
        "tool reported that a full sync is required and the user picked a direction.",
        annotations=DESTRUCTIVE,
    )
    async def full_sync(
        direction: Literal["download", "upload"],
        confirm: Annotated[bool, Field(description="Must be true; set it once the user agreed")] = False,
    ) -> dict[str, Any]:
        if not confirm:
            raise ToolError("Ask the user first, then call again with confirm=true.")
        return await call(partial(session.full_sync, direction))

    # Decks
    ######################################################################

    @server.tool(
        title="List decks",
        description="All decks with their ids, card counts and what is due today.",
        annotations=READ,
    )
    async def list_decks(
        name_contains: Annotated[str | None, Field(description="Only decks whose name contains this")] = None,
    ) -> dict[str, Any]:
        return await read(lambda col: ops.list_decks(col, name_contains))

    @server.tool(
        title="Create a deck",
        description="Creates a deck, including missing parent decks. Returns the existing deck "
        "if there already is one with that name. add_notes creates its deck on its own, so this "
        "is only needed for empty decks or to set a description.",
        annotations=WRITE,
    )
    async def create_deck(
        name: Annotated[str, Field(description="Deck name; :: makes subdecks, e.g. 'OTH::Mathematik 1'")],
        description: Annotated[str | None, Field(description="Shown on the deck's overview page")] = None,
    ) -> dict[str, Any]:
        return await write(lambda col: ops.create_deck(col, name, description))

    @server.tool(
        title="Rename or move a deck",
        description="Renames a deck. A new name with :: moves it under another deck. Subdecks move along.",
        annotations=WRITE,
    )
    async def rename_deck(deck: Deck, new_name: str) -> dict[str, Any]:
        return await write(lambda col: ops.rename_deck(col, deck, new_name))

    @server.tool(
        title="Delete a deck",
        description="Deletes a deck and its subdecks. Cards in them are deleted too, together "
        "with their review history, so this refuses unless delete_cards is true. Confirm with "
        "the user before setting it.",
        annotations=DESTRUCTIVE,
    )
    async def delete_deck(deck: Deck, delete_cards: bool = False) -> dict[str, Any]:
        return await write(lambda col: ops.delete_deck(col, deck, delete_cards))

    # Note types
    ######################################################################

    @server.tool(
        title="List note types",
        description="Note types with their field names, which is what add_notes needs. Pass a "
        "name to get that note type's card templates and CSS as well.",
        annotations=READ,
    )
    async def list_note_types(
        name: Annotated[str | None, Field(description="Show one note type in full")] = None,
    ) -> dict[str, Any]:
        return await read(lambda col: ops.list_note_types(col, name))

    @server.tool(
        title="Create a note type",
        description="Creates a note type with its fields, card templates and CSS. Each template "
        "produces one card per note. Templates reference fields as {{Field name}}; the back "
        "usually starts with {{FrontSide}}<hr id=answer>. For a cloze type set cloze=true and "
        "use {{cloze:Text}} in the one template. The built-in Basic and Cloze types cover most "
        "decks; create one only if the cards need extra fields or their own look.",
        annotations=WRITE,
    )
    async def create_note_type(
        name: str,
        fields: Annotated[list[str], Field(description="Field names in order; the first one identifies a note")],
        templates: list[Template],
        css: Annotated[str | None, Field(description="Styling shared by all cards of this type")] = None,
        cloze: bool = False,
    ) -> dict[str, Any]:
        specs = [t.model_dump() for t in templates]
        return await write(lambda col: ops.create_note_type(col, name, fields, specs, css, cloze))

    @server.tool(
        title="Restyle a note type",
        description="Changes the template HTML or the CSS of an existing note type. Every card "
        "of that type changes its look. Fields and the set of templates cannot be changed here: "
        "Anki forces a full sync on all devices for that, so do it in Anki itself.",
        annotations=WRITE,
    )
    async def update_note_type(
        note_type: Annotated[str, Field(description="Note type name or id")],
        templates: list[TemplateUpdate] | None = None,
        css: str | None = None,
    ) -> dict[str, Any]:
        specs = [t.model_dump() for t in templates or []]
        return await write(lambda col: ops.update_note_type(col, note_type, specs, css))

    # Notes
    ######################################################################

    @server.tool(
        title="Add notes",
        description="Adds notes (cards) to a deck, any number in one call, then syncs. This is "
        "the tool for creating cards in bulk. A note whose first field already exists in the "
        "same note type counts as a duplicate: 'skip' leaves the old one alone, 'update' "
        "overwrites its other fields, 'allow' adds a second copy. Notes that cannot be added "
        "are listed under problems with their index; the rest still go in.",
        annotations=WRITE,
    )
    async def add_notes(
        deck: Deck,
        notes: Annotated[list[NoteInput], Field(description="The notes to add")],
        note_type: Annotated[str, Field(description="Note type name: Basic, Cloze, or one from list_note_types")] = "Basic",
        tags: Annotated[list[str] | None, Field(description="Tags added to every note of this call")] = None,
        on_duplicate: Literal["skip", "update", "allow"] = "skip",
        create_deck: Annotated[bool, Field(description="Create the deck if it does not exist")] = True,
    ) -> dict[str, Any]:
        items = [n.model_dump() for n in notes]
        return await write(
            lambda col: ops.add_notes(
                col,
                deck=deck,
                notes=items,
                note_type=note_type,
                tags=tags,
                on_duplicate=on_duplicate,
                create_deck=create_deck,
            )
        )

    @server.tool(
        title="Find notes",
        description="Searches notes with Anki's browser syntax and returns their fields, tags, "
        "decks and card ids, newest first. Long fields are cut at max_field_chars; get_notes "
        "returns them in full.",
        annotations=READ,
    )
    async def find_notes(
        query: Annotated[str, Field(description='Anki search, e.g. deck:"Mathe 1" tag:vl03 or "front:*Folge*"')],
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_field_chars: Annotated[int, Field(ge=0, description="0 returns fields in full")] = 300,
    ) -> dict[str, Any]:
        return await read(lambda col: ops.find_notes(col, query, limit, offset, max_field_chars))

    @server.tool(
        title="Get notes",
        description="Returns notes by id with their complete field contents.",
        annotations=READ,
    )
    async def get_notes(note_ids: list[int]) -> dict[str, Any]:
        return await read(lambda col: ops.get_notes(col, note_ids))

    @server.tool(
        title="Edit notes",
        description="Changes fields and tags of existing notes, any number in one call. Fields "
        "you leave out keep their content. Review history and scheduling stay as they are.",
        annotations=WRITE,
    )
    async def update_notes(updates: list[NoteUpdate]) -> dict[str, Any]:
        items = [u.model_dump() for u in updates]
        return await write(lambda col: ops.update_notes(col, items))

    @server.tool(
        title="Delete notes",
        description="Deletes notes with all their cards and review history, on every device "
        "after it syncs. This cannot be undone from here. Show the user what will be deleted first.",
        annotations=DESTRUCTIVE,
    )
    async def delete_notes(note_ids: list[int]) -> dict[str, Any]:
        return await write(lambda col: ops.delete_notes(col, note_ids))

    # Cards
    ######################################################################

    @server.tool(
        title="Find cards",
        description="Searches cards with Anki's browser syntax and returns their deck, state "
        "(new, learning, review), due date, interval, ease and lapses. Use it for questions "
        "about scheduling, e.g. is:due, is:suspended, prop:lapses>3, rated:1.",
        annotations=READ,
    )
    async def find_cards(
        query: Annotated[str, Field(description='Anki search, e.g. deck:"Mathe 1" is:due')],
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        return await read(lambda col: ops.find_cards(col, query, limit, offset))

    @server.tool(
        title="Move cards to a deck",
        description="Moves cards to another deck, picked by id, by search, or both. Their "
        "scheduling stays. The target deck is created if it does not exist.",
        annotations=WRITE,
    )
    async def move_cards(deck: Deck, card_ids: CardIds = None, query: Query = None) -> dict[str, Any]:
        return await write(lambda col: ops.move_cards(col, deck, card_ids, query))

    @server.tool(
        title="Suspend or unsuspend cards",
        description="Suspended cards stay in the deck but are not shown in reviews until they "
        "are unsuspended. Pick cards by id, by search, or both.",
        annotations=WRITE,
    )
    async def suspend_cards(
        card_ids: CardIds = None,
        query: Query = None,
        suspended: Annotated[bool, Field(description="false brings the cards back")] = True,
    ) -> dict[str, Any]:
        return await write(lambda col: ops.set_suspended(col, suspended, card_ids, query))

    # Tags
    ######################################################################

    @server.tool(title="List tags", description="All tags used in the collection.", annotations=READ)
    async def list_tags() -> dict[str, Any]:
        return await read(ops.list_tags)

    @server.tool(
        title="Tag notes",
        description="Adds or removes tags on many notes at once, picked by id, by search, or "
        "both. Spaces inside a tag become underscores; :: makes a tag hierarchy.",
        annotations=WRITE,
    )
    async def update_tags(
        note_ids: Annotated[list[int] | None, Field(description="Note ids, e.g. from find_notes")] = None,
        query: Query = None,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict[str, Any]:
        return await write(lambda col: ops.update_tags(col, note_ids, query, add, remove))

    # Media and packages
    ######################################################################

    @server.tool(
        title="Store an image or audio file",
        description="Copies a file into the collection's media folder and returns the snippet "
        "to put into a note field. Anki may rename the file, so use the returned name. Media "
        "syncs after the call; the first media sync downloads the account's existing media library.",
        annotations=WRITE,
    )
    async def store_media(
        path: Annotated[str | None, Field(description="Path of a local file")] = None,
        data_base64: Annotated[str | None, Field(description="File content, if there is no local file")] = None,
        filename: Annotated[str | None, Field(description="Name to store it under; required with data_base64")] = None,
    ) -> dict[str, Any]:
        return await write(lambda col: ops.store_media(col, filename, path, data_base64), adds_media=True)

    @server.tool(
        title="Import an .apkg file",
        description="Imports an Anki package from disk into the collection and syncs, so a deck "
        "another tool exported lands in the account without a manual import. Notes that already "
        "exist are updated or left alone depending on update_notes; importing the same file "
        "twice does not duplicate cards.",
        annotations=WRITE,
    )
    async def import_package(
        path: Annotated[str, Field(description="Path of the .apkg file")],
        update_notes: Annotated[
            Literal["if_newer", "always", "never"],
            Field(description="What to do with notes that already exist in the collection"),
        ] = "if_newer",
        with_scheduling: Annotated[
            bool, Field(description="Also import review history and due dates stored in the package")
        ] = False,
    ) -> dict[str, Any]:
        return await write(
            lambda col: ops.import_package(col, path, update_notes, with_scheduling), adds_media=True
        )

    @server.tool(
        title="Export a deck as .apkg",
        description="Writes a deck with its subdecks to an .apkg file, for sharing or as a backup.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    )
    async def export_package(
        deck: Deck,
        path: Annotated[str, Field(description="Where to write the file")],
        with_scheduling: Annotated[bool, Field(description="Include review history and due dates")] = False,
        with_media: bool = True,
        legacy: Annotated[bool, Field(description="Write the old format that Anki before 2.1.50 can read")] = False,
    ) -> dict[str, Any]:
        return await read(lambda col: ops.export_package(col, deck, path, with_scheduling, with_media, legacy))

    return server
