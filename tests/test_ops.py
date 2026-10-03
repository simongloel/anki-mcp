from __future__ import annotations

import base64

import pytest
from anki.collection import Collection

from anki_mcp import ops
from anki_mcp.session import AnkiMcpError


def basic(front: str, back: str = "answer", **extra: object) -> dict:
    return {"fields": {"Front": front, "Back": back}, **extra}


def test_to_html_keeps_line_breaks_of_plain_text_only() -> None:
    assert ops.to_html("a\nb") == "a<br>b"
    assert ops.to_html("<ul>\n<li>a</li>\n</ul>") == "<ul>\n<li>a</li>\n</ul>"
    assert ops.to_html("if a < b") == "if a < b"


def test_clean_tags() -> None:
    assert ops.clean_tags(["Mathe 1", " vl03 ", "", "vl03"]) == ["Mathe_1", "vl03"]


def test_add_notes_creates_deck_and_cards(col: Collection) -> None:
    result = ops.add_notes(
        col,
        deck="OTH :: Mathematik 1",
        notes=[basic("Was ist eine Folge?", "Eine Abbildung\nvon N nach R", tags=["vl03"]), basic("q2")],
        tags=["mathe 1"],
    )
    assert result["added"] == 2 and result["failed"] == 0
    assert result["deck"] == "OTH::Mathematik 1" and result["deck_created"]
    note = ops.get_notes(col, result["note_ids"])["notes"][0]
    assert note["fields"] == {"Front": "Was ist eine Folge?", "Back": "Eine Abbildung<br>von N nach R"}
    assert note["tags"] == ["mathe_1", "vl03"]
    assert note["decks"] == ["OTH::Mathematik 1"]
    assert len(note["card_ids"]) == 1


def test_add_notes_reports_bad_notes_and_adds_the_rest(col: Collection) -> None:
    result = ops.add_notes(
        col,
        deck="D",
        notes=[
            basic("ok"),
            {"fields": {"Question": "x"}},
            {"fields": {"Back": "no front"}},
            basic("{{c1::cloze}} in a basic note"),
        ],
    )
    assert result["added"] == 1 and result["failed"] == 3
    reasons = {p["index"]: p["reason"] for p in result["problems"]}
    assert "unknown field" in reasons[1]
    assert "first field is empty" in reasons[2]
    assert "Cloze" in reasons[3]


def test_cloze_notes(col: Collection) -> None:
    result = ops.add_notes(
        col,
        deck="D",
        note_type="cloze",
        notes=[
            {"fields": {"Text": "{{c1::Paris}} liegt an der {{c2::Seine}}"}},
            {"fields": {"Text": "no deletion"}},
        ],
    )
    assert result["note_type"] == "Cloze"
    assert result["added"] == 1 and result["failed"] == 1
    assert len(ops.get_notes(col, result["note_ids"])["notes"][0]["card_ids"]) == 2


def test_duplicates_skip_update_allow(col: Collection) -> None:
    first = ops.add_notes(col, deck="D", notes=[basic("q", "old")])
    nid = first["note_ids"][0]

    skipped = ops.add_notes(col, deck="D", notes=[basic("q", "new"), basic("x"), basic("x")])
    assert skipped["added"] == 1 and skipped["skipped_duplicates"] == 2
    assert [p["index"] for p in skipped["problems"]] == [0, 2]
    assert str(nid) in skipped["problems"][0]["reason"]

    updated = ops.add_notes(col, deck="D", notes=[basic("<b>q</b>", "new", tags=["t"])], on_duplicate="update")
    assert updated["added"] == 0 and updated["updated_note_ids"] == [nid]
    note = ops.get_notes(col, [nid])["notes"][0]
    assert note["fields"]["Back"] == "new" and note["tags"] == ["t"]

    allowed = ops.add_notes(col, deck="D", notes=[basic("q")], on_duplicate="allow")
    assert allowed["added"] == 1
    assert col.note_count() == 3


def test_add_notes_can_refuse_to_create_a_deck(col: Collection) -> None:
    with pytest.raises(AnkiMcpError, match="no deck named"):
        ops.add_notes(col, deck="Missing", notes=[basic("q")], create_deck=False)
    with pytest.raises(AnkiMcpError, match="no note type"):
        ops.add_notes(col, deck="D", notes=[basic("q")], note_type="Nope")


def test_bulk_add(col: Collection) -> None:
    notes = [basic(f"question {i}", f"answer {i}") for i in range(500)]
    result = ops.add_notes(col, deck="Bulk", notes=notes)
    assert result["added"] == 500
    found = ops.find_notes(col, 'deck:"Bulk"', limit=20, offset=490)
    assert found["total"] == 500 and found["returned"] == 10


def test_update_and_delete_notes(col: Collection) -> None:
    ids = ops.add_notes(col, deck="D", notes=[basic("a", tags=["x", "y"]), basic("b")])["note_ids"]
    result = ops.update_notes(
        col,
        [
            {"id": ids[0], "fields": {"back": "changed"}, "add_tags": ["z"], "remove_tags": ["X"]},
            {"id": ids[1], "fields": {"Nope": "1"}},
            {"id": 42, "fields": {"Back": "1"}},
        ],
    )
    assert result["updated"] == 1 and result["failed"] == 2
    note = ops.get_notes(col, [ids[0]])["notes"][0]
    assert note["fields"] == {"Front": "a", "Back": "changed"} and note["tags"] == ["y", "z"]

    deleted = ops.delete_notes(col, [*ids, 42])
    assert deleted == {"deleted_notes": 2, "deleted_cards": 2, "not_found": [42]}
    assert col.note_count() == 0


def test_find_notes_truncates_long_fields(col: Collection) -> None:
    ops.add_notes(col, deck="D", notes=[basic("q", "x" * 1000)])
    short = ops.find_notes(col, "deck:D", max_field_chars=10)["notes"][0]
    assert short["truncated"] and len(short["fields"]["Back"]) == 11
    full = ops.find_notes(col, "deck:D", max_field_chars=0)["notes"][0]
    assert len(full["fields"]["Back"]) == 1000 and "truncated" not in full


def test_decks(col: Collection) -> None:
    created = ops.create_deck(col, "Uni::Mathe", description="Analysis 1")
    assert created["created"]
    assert not ops.create_deck(col, "uni::mathe")["created"]
    ops.add_notes(col, deck="Uni::Mathe", notes=[basic("q")])

    names = [d["name"] for d in ops.list_decks(col)["decks"]]
    assert "Uni" in names and "Uni::Mathe" in names
    mathe = ops.list_decks(col, "mathe")["decks"][0]
    assert mathe["cards"] == 1 and mathe["due_new"] == 1

    renamed = ops.rename_deck(col, "Uni::Mathe", "Uni::Mathematik 1")
    assert renamed["name"] == "Uni::Mathematik 1"

    with pytest.raises(AnkiMcpError, match="holds 1 cards"):
        ops.delete_deck(col, "Uni")
    with pytest.raises(AnkiMcpError, match="Default"):
        ops.delete_deck(col, 1)
    deleted = ops.delete_deck(col, "Uni", delete_cards=True)
    assert deleted["deleted_cards"] == 1 and deleted["deleted_subdecks"] == ["Uni::Mathematik 1"]
    assert col.note_count() == 0


def test_unknown_deck_suggests_similar_names(col: Collection) -> None:
    ops.create_deck(col, "Mathematik 1")
    with pytest.raises(AnkiMcpError, match="Did you mean: Mathematik 1"):
        ops.resolve_deck(col, "Mathe")


def test_cards_move_suspend_find(col: Collection) -> None:
    ops.add_notes(col, deck="A", notes=[basic("one"), basic("two")], tags=["t"])
    cards = ops.find_cards(col, "deck:A")
    assert cards["total"] == 2
    card = cards["cards"][0]
    assert card["state"] == "new" and card["deck"] == "A" and card["card_type"] == "Card 1"

    moved = ops.move_cards(col, "B::Sub", query="deck:A")
    assert moved["moved"] == 2 and moved["deck_created"]
    assert ops.find_cards(col, 'deck:"B::Sub"')["total"] == 2

    assert ops.set_suspended(col, True, card_ids=[card["id"]]) == {"suspended": 1}
    assert ops.find_cards(col, "is:suspended")["cards"][0]["suspended"]
    assert ops.set_suspended(col, False, query="is:suspended") == {"unsuspended": 1}

    with pytest.raises(AnkiMcpError, match="card_ids, a search query"):
        ops.move_cards(col, "B")
    with pytest.raises(AnkiMcpError, match="do not exist"):
        ops.set_suspended(col, True, card_ids=[1])


def test_tags(col: Collection) -> None:
    ids = ops.add_notes(col, deck="D", notes=[basic("a"), basic("b")], tags=["old"])["note_ids"]
    result = ops.update_tags(col, query="deck:D", add=["exam 2027"], remove=["old"])
    assert result["notes"] == 2
    assert ops.get_notes(col, ids)["notes"][0]["tags"] == ["exam_2027"]
    assert "exam_2027" in ops.list_tags(col)["tags"]


def test_note_types_do_not_touch_the_schema(col: Collection) -> None:
    """A changed schema timestamp would force a full sync on every device."""
    schema = col.db.scalar("select scm from col")
    created = ops.create_note_type(
        col,
        "Vocab",
        fields=["Word", "Meaning", "Example"],
        templates=[
            {"name": "Forward", "front": "{{Word}}", "back": "{{FrontSide}}<hr id=answer>{{Meaning}}"},
            {"name": "Reverse", "front": "{{Meaning}}", "back": "{{FrontSide}}<hr id=answer>{{Word}}"},
        ],
        css=".card { font-size: 22px; }",
    )
    assert created["fields"] == ["Word", "Meaning", "Example"]
    added = ops.add_notes(col, deck="V", note_type="Vocab", notes=[{"fields": {"Word": "Haus", "Meaning": "house"}}])
    assert len(ops.get_notes(col, added["note_ids"])["notes"][0]["card_ids"]) == 2

    updated = ops.update_note_type(
        col, "vocab", templates=[{"name": "forward", "front": "<b>{{Word}}</b>", "back": None}], css=".card {}"
    )
    assert updated["templates"][0]["front"] == "<b>{{Word}}</b>"
    assert updated["templates"][0]["back"].startswith("{{FrontSide}}")
    assert updated["css"] == ".card {}" and updated["notes"] == 1
    assert col.db.scalar("select scm from col") == schema

    listing = ops.list_note_types(col)
    assert {"Basic", "Cloze", "Vocab"} <= {t["name"] for t in listing["note_types"]}
    with pytest.raises(AnkiMcpError, match="already exists"):
        ops.create_note_type(col, "Vocab", ["A"], [{"front": "{{A}}", "back": "{{A}}"}])
    with pytest.raises(AnkiMcpError, match="no template named"):
        ops.update_note_type(col, "Vocab", templates=[{"name": "Card 9", "front": "x", "back": None}])


def test_store_media(col: Collection, tmp_path) -> None:
    image = tmp_path / "graph.png"
    image.write_bytes(b"\x89PNG fake")
    stored = ops.store_media(col, path=str(image))
    assert stored["filename"] == "graph.png" and stored["use_in_field"] == '<img src="graph.png">'
    assert col.media.have("graph.png")

    # same name, other content: Anki stores it under a new name
    other = ops.store_media(col, filename="graph.png", data_base64=base64.b64encode(b"other").decode())
    assert other["filename"] != "graph.png" and col.media.have(other["filename"])

    audio = ops.store_media(col, filename="word.mp3", data_base64=base64.b64encode(b"id3").decode())
    assert audio["use_in_field"] == "[sound:word.mp3]"
    with pytest.raises(AnkiMcpError, match="either path or data_base64"):
        ops.store_media(col, filename="x.png")


def test_export_then_import_does_not_duplicate(col: Collection, tmp_path) -> None:
    ops.add_notes(col, deck="Share::Me", notes=[basic("a"), basic("b")])
    exported = ops.export_package(col, "Share", str(tmp_path / "out" / "share"))
    assert exported["notes"] == 2 and exported["file"].endswith("share.apkg")

    again = ops.import_package(col, exported["file"])
    assert again["added"] == 0 and again["notes_in_package"] == 2
    assert col.note_count() == 2

    ops.delete_deck(col, "Share", delete_cards=True)
    fresh = ops.import_package(col, exported["file"])
    assert fresh["added"] == 2 and "Share::Me" in fresh["new_decks"]
    with pytest.raises(AnkiMcpError, match="no file"):
        ops.import_package(col, str(tmp_path / "missing.apkg"))
