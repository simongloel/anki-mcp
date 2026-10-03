"""The tools as an MCP client sees them."""

from __future__ import annotations

from pathlib import Path

import anyio
from mcp.client import Client

from anki_mcp.server import build_server

from .conftest import make_config


def run(tmp_path: Path, endpoint: str, scenario) -> None:
    async def main() -> None:
        async with Client(build_server(make_config(tmp_path / "mcp", endpoint))) as client:
            await scenario(client)

    anyio.run(main)


def test_tool_list(tmp_path: Path, sync_server: str) -> None:
    async def scenario(client: Client) -> None:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert {"add_notes", "find_notes", "update_notes", "delete_notes", "create_deck", "import_package", "sync"} <= set(tools)
        assert all(t.description and t.annotations for t in tools.values())
        assert tools["find_notes"].annotations.read_only_hint is True
        assert tools["delete_notes"].annotations.destructive_hint is True
        notes = tools["add_notes"].input_schema["properties"]["notes"]
        assert notes["type"] == "array"

    run(tmp_path, sync_server, scenario)


def test_add_find_edit_delete_through_mcp(tmp_path: Path, sync_server: str) -> None:
    async def scenario(client: Client) -> None:
        added = await client.call_tool(
            "add_notes",
            {
                "deck": "OTH::Mathematik 1::VL03 Folgen",
                "tags": ["mathe1", "vl03"],
                "notes": [
                    {"fields": {"Front": "Wann heißt eine Folge konvergent?", "Back": "Wenn sie einen Grenzwert hat."}},
                    {"fields": {"Front": "Was ist eine Nullfolge?", "Back": "Eine Folge mit Grenzwert 0."}},
                ],
            },
        )
        assert not added.is_error
        data = added.structured_content
        assert data["added"] == 2 and data["sync"]["status"] == "full_sync_required"

        uploaded = await client.call_tool("full_sync", {"direction": "upload"})
        assert uploaded.is_error and "confirm=true" in uploaded.content[0].text
        uploaded = await client.call_tool("full_sync", {"direction": "upload", "confirm": True})
        assert uploaded.structured_content["status"] == "synced"

        found = await client.call_tool("find_notes", {"query": 'deck:"OTH::Mathematik 1::VL03 Folgen" tag:vl03'})
        assert found.structured_content["total"] == 2 and "sync" not in found.structured_content

        nid = data["note_ids"][0]
        edited = await client.call_tool("update_notes", {"updates": [{"id": nid, "fields": {"Back": "neu"}}]})
        assert edited.structured_content == {"updated": 1, "note_ids": [nid], "sync": {"status": "synced"}}

        deleted = await client.call_tool("delete_notes", {"note_ids": data["note_ids"]})
        assert deleted.structured_content["deleted_notes"] == 2

        status = await client.call_tool("status", {})
        assert status.structured_content["notes"] == 0 and status.structured_content["signed_in"]

    run(tmp_path, sync_server, scenario)


def test_errors_reach_the_client_as_readable_text(tmp_path: Path, sync_server: str) -> None:
    async def scenario(client: Client) -> None:
        bad_search = await client.call_tool("find_notes", {"query": "front:(unbalanced"})
        assert bad_search.is_error
        missing = await client.call_tool("delete_deck", {"deck": "Nope"})
        assert missing.is_error and "no deck named" in missing.content[0].text
        invalid = await client.call_tool("add_notes", {"deck": "D", "notes": [{"front": "x"}]})
        assert invalid.is_error

    run(tmp_path, sync_server, scenario)


def test_signed_out_server_explains_how_to_sign_in(tmp_path: Path, sync_server: str) -> None:
    async def main() -> None:
        cfg = make_config(tmp_path / "signed-out", sync_server, username=None, password=None)
        async with Client(build_server(cfg)) as client:
            result = await client.call_tool("list_decks", {})
            assert result.is_error and "ANKIWEB_USERNAME" in result.content[0].text
            status = await client.call_tool("status", {})
            assert status.structured_content["signed_in"] is False

    anyio.run(main)
