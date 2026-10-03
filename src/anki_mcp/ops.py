"""What the tools do to an open collection.

Nothing in here syncs or opens files; session.py wraps these calls.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import os
import re
from collections.abc import Iterable, Sequence
from typing import Any

from anki.cards import Card
from anki.collection import (
    AddNoteRequest,
    Collection,
    DeckIdLimit,
    ExportAnkiPackageOptions,
    ImportAnkiPackageOptions,
    ImportAnkiPackageRequest,
)
from anki.consts import (
    CARD_TYPE_LRN,
    CARD_TYPE_NEW,
    CARD_TYPE_RELEARNING,
    CARD_TYPE_REV,
    MODEL_CLOZE,
    QUEUE_TYPE_DAY_LEARN_RELEARN,
    QUEUE_TYPE_MANUALLY_BURIED,
    QUEUE_TYPE_SIBLING_BURIED,
    QUEUE_TYPE_SUSPENDED,
)
from anki.import_export_pb2 import ImportAnkiPackageUpdateCondition
from anki.notes import Note
from anki.notes_pb2 import NoteFieldsCheckResponse
from anki.utils import field_checksum, strip_html, strip_html_media

from .session import AnkiMcpError

DEFAULT_DECK_ID = 1
FieldCheck = NoteFieldsCheckResponse.State

_HTML_TAG = re.compile(r"<[a-zA-Z/!][^>]*>")
_CARD_STATES = {
    CARD_TYPE_NEW: "new",
    CARD_TYPE_LRN: "learning",
    CARD_TYPE_REV: "review",
    CARD_TYPE_RELEARNING: "relearning",
}
_UPDATE_CONDITIONS = {
    "if_newer": ImportAnkiPackageUpdateCondition.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_IF_NEWER,
    "always": ImportAnkiPackageUpdateCondition.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_ALWAYS,
    "never": ImportAnkiPackageUpdateCondition.IMPORT_ANKI_PACKAGE_UPDATE_CONDITION_NEVER,
}


# Helpers
##########################################################################


def to_html(text: str) -> str:
    """Anki fields are HTML. Plain text only needs its line breaks kept."""
    if _HTML_TAG.search(text):
        return text
    return text.replace("\r\n", "\n").replace("\n", "<br>")


def clean_tags(tags: Iterable[str] | None) -> list[str]:
    """Anki separates tags with spaces, so a tag cannot contain one."""
    out: list[str] = []
    for tag in tags or ():
        tag = "_".join(str(tag).split())
        if tag and tag not in out:
            out.append(tag)
    return out


def _deck_name(name: str) -> str:
    return "::".join(part.strip() for part in name.split("::") if part.strip())


def _preview(html: str, limit: int = 80) -> str:
    text = " ".join(strip_html(html.replace("<br>", " ")).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def resolve_deck(col: Collection, deck: str | int) -> tuple[int, str]:
    """Find a deck by id or name. Names compare case-insensitively, as in Anki."""
    if isinstance(deck, int) or (isinstance(deck, str) and deck.strip().isdigit()):
        did = int(deck)
        name = col.decks.name_if_exists(did)
        if name is not None:
            return did, name
    name = _deck_name(str(deck))
    if not name:
        raise AnkiMcpError("The deck needs a name.")
    did = col.decks.id_for_name(name)
    if not did:
        close = [d.name for d in col.decks.all_names_and_ids() if name.lower() in d.name.lower()]
        hint = f" Did you mean: {', '.join(close[:8])}?" if close else " list_decks shows the decks that exist."
        raise AnkiMcpError(f"There is no deck named {name!r}.{hint}")
    return did, col.decks.name(did)


def _deck_for_adding(col: Collection, deck: str | int, create: bool) -> tuple[int, str, bool]:
    try:
        did, name = resolve_deck(col, deck)
        return did, name, False
    except AnkiMcpError:
        if not create or isinstance(deck, int) or str(deck).strip().isdigit():
            raise
    did = col.decks.id(_deck_name(str(deck)), create=True)
    return did, col.decks.name(did), True


def resolve_note_type(col: Collection, note_type: str | int) -> dict[str, Any]:
    model = None
    if isinstance(note_type, int) or str(note_type).strip().isdigit():
        model = col.models.get(int(note_type))
    if model is None:
        wanted = str(note_type).strip()
        model = col.models.by_name(wanted)
        if model is None:
            for entry in col.models.all_names_and_ids():
                if entry.name.lower() == wanted.lower():
                    model = col.models.get(entry.id)
                    break
    if model is None:
        names = ", ".join(sorted(e.name for e in col.models.all_names_and_ids()))
        raise AnkiMcpError(f"There is no note type named {note_type!r}. Available: {names}")
    return model


def _select_cards(col: Collection, card_ids: Sequence[int] | None, query: str | None) -> list[int]:
    if not card_ids and not (query and query.strip()):
        raise AnkiMcpError("Pass card_ids, a search query, or both.")
    ids: list[int] = []
    if card_ids:
        wanted = [int(c) for c in card_ids]
        existing = set(col.db.list(f"select id from cards where id in ({_id_list(wanted)})"))
        missing = [c for c in wanted if c not in existing]
        if missing:
            raise AnkiMcpError(f"These card ids do not exist: {missing[:20]}")
        ids.extend(wanted)
    if query and query.strip():
        ids.extend(col.find_cards(query))
    return list(dict.fromkeys(ids))


def _select_notes(col: Collection, note_ids: Sequence[int] | None, query: str | None) -> list[int]:
    if not note_ids and not (query and query.strip()):
        raise AnkiMcpError("Pass note_ids, a search query, or both.")
    ids: list[int] = []
    if note_ids:
        wanted = [int(n) for n in note_ids]
        existing = set(col.db.list(f"select id from notes where id in ({_id_list(wanted)})"))
        missing = [n for n in wanted if n not in existing]
        if missing:
            raise AnkiMcpError(f"These note ids do not exist: {missing[:20]}")
        ids.extend(wanted)
    if query and query.strip():
        ids.extend(col.find_notes(query))
    return list(dict.fromkeys(ids))


def _id_list(ids: Iterable[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


# Decks
##########################################################################


def list_decks(col: Collection, name_contains: str | None = None) -> dict[str, Any]:
    decks: list[dict[str, Any]] = []

    def walk(node: Any, parents: list[str]) -> None:
        for child in node.children:
            path = [*parents, child.name]
            decks.append(
                {
                    "id": child.deck_id,
                    "name": "::".join(path),
                    "cards": child.total_in_deck,
                    "cards_with_subdecks": child.total_including_children,
                    "due_new": child.new_count,
                    "due_learning": child.learn_count,
                    "due_review": child.review_count,
                    **({"filtered": True} if child.filtered else {}),
                }
            )
            walk(child, path)

    walk(col.sched.deck_due_tree(), [])
    if name_contains:
        needle = name_contains.lower()
        decks = [d for d in decks if needle in d["name"].lower()]
    return {"count": len(decks), "decks": decks}


def create_deck(col: Collection, name: str, description: str | None = None) -> dict[str, Any]:
    did, full_name, created = _deck_for_adding(col, name, create=True)
    if description is not None:
        deck = col.decks.get(did, default=False)
        if deck is not None and deck.get("desc") != description:
            deck["desc"] = description
            col.decks.update_dict(deck)
    return {"id": did, "name": full_name, "created": created}


def rename_deck(col: Collection, deck: str | int, new_name: str) -> dict[str, Any]:
    did, old = resolve_deck(col, deck)
    new_name = _deck_name(new_name)
    if not new_name:
        raise AnkiMcpError("The deck needs a name.")
    col.decks.rename(did, new_name)
    return {"id": did, "old_name": old, "name": col.decks.name(did)}


def delete_deck(col: Collection, deck: str | int, delete_cards: bool = False) -> dict[str, Any]:
    did, name = resolve_deck(col, deck)
    if did == DEFAULT_DECK_ID:
        raise AnkiMcpError("Anki does not allow deleting the Default deck.")
    cards = col.decks.card_count(did, include_subdecks=True)
    subdecks = [n for n, _ in col.decks.children(did)]
    if cards and not delete_cards:
        raise AnkiMcpError(
            f"{name!r} holds {cards} cards"
            + (f" in {len(subdecks) + 1} decks" if subdecks else "")
            + ". Deleting the deck deletes them too. Ask the user, then call again with "
            "delete_cards=true, or move the cards first with move_cards."
        )
    col.decks.remove([did])
    return {"deleted": name, "deleted_subdecks": subdecks, "deleted_cards": cards}


# Note types
##########################################################################


def _note_type_info(model: dict[str, Any], notes: int | None, templates: bool) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": model["id"],
        "name": model["name"],
        "kind": "cloze" if model["type"] == MODEL_CLOZE else "standard",
        "fields": [f["name"] for f in model["flds"]],
    }
    if notes is not None:
        info["notes"] = notes
    if templates:
        info["templates"] = [
            {"name": t["name"], "front": t["qfmt"], "back": t["afmt"]} for t in model["tmpls"]
        ]
        info["css"] = model["css"]
    else:
        info["card_types"] = [t["name"] for t in model["tmpls"]]
    return info


def list_note_types(col: Collection, name: str | None = None) -> dict[str, Any]:
    """All note types, or one of them with its templates and CSS."""
    use = {u.id: u.use_count for u in col.models.all_use_counts()}
    if name:
        model = resolve_note_type(col, name)
        return _note_type_info(model, use.get(model["id"]), templates=True)
    types = [_note_type_info(m, use.get(m["id"]), templates=False) for m in col.models.all()]
    types.sort(key=lambda t: t["name"].lower())
    return {"count": len(types), "note_types": types}


def create_note_type(
    col: Collection,
    name: str,
    fields: Sequence[str],
    templates: Sequence[dict[str, str]],
    css: str | None = None,
    cloze: bool = False,
) -> dict[str, Any]:
    name = name.strip()
    fields = [f.strip() for f in fields if f.strip()]
    if not name or not fields or not templates:
        raise AnkiMcpError("A note type needs a name, at least one field and at least one template.")
    if col.models.id_for_name(name):
        raise AnkiMcpError(f"A note type named {name!r} already exists.")
    if cloze and len(templates) != 1:
        raise AnkiMcpError("A cloze note type has exactly one template.")
    models = col.models
    model = models.new(name)
    if cloze:
        model["type"] = MODEL_CLOZE
    for field in fields:
        models.add_field(model, models.new_field(field))
    for i, spec in enumerate(templates):
        template = models.new_template(spec.get("name") or f"Card {i + 1}")
        template["qfmt"] = spec["front"]
        template["afmt"] = spec["back"]
        models.add_template(model, template)
    if css is not None:
        model["css"] = css
    out = models.add_dict(model)
    return _note_type_info(models.get(out.id), 0, templates=True)


def update_note_type(
    col: Collection,
    note_type: str | int,
    templates: Sequence[dict[str, str]] | None = None,
    css: str | None = None,
) -> dict[str, Any]:
    """Change template HTML and CSS.

    Adding or removing fields and templates is left to Anki itself: those
    changes force a one-way full sync on every device.
    """
    model = resolve_note_type(col, note_type)
    by_name = {t["name"].lower(): t for t in model["tmpls"]}
    for spec in templates or ():
        template = by_name.get(str(spec.get("name", "")).lower())
        if template is None:
            raise AnkiMcpError(
                f"{model['name']!r} has no template named {spec.get('name')!r}. "
                f"It has: {', '.join(t['name'] for t in model['tmpls'])}"
            )
        if spec.get("front") is not None:
            template["qfmt"] = spec["front"]
        if spec.get("back") is not None:
            template["afmt"] = spec["back"]
    if css is not None:
        model["css"] = css
    col.models.update_dict(model)
    use = {u.id: u.use_count for u in col.models.all_use_counts()}
    return _note_type_info(col.models.get(model["id"]), use.get(model["id"]), templates=True)


# Notes
##########################################################################


def _note_info(col: Collection, note: Note, max_field_chars: int | None = None) -> dict[str, Any]:
    fields: dict[str, str] = {}
    truncated = False
    for name, value in note.items():
        if max_field_chars and len(value) > max_field_chars:
            value = value[:max_field_chars] + "…"
            truncated = True
        fields[name] = value
    rows = col.db.all("select id, did from cards where nid = ? order by ord", note.id)
    info: dict[str, Any] = {
        "id": note.id,
        "note_type": note.note_type()["name"],
        "fields": fields,
        "tags": list(note.tags),
        "decks": sorted({col.decks.name(did) for _, did in rows}),
        "card_ids": [cid for cid, _ in rows],
    }
    if truncated:
        info["truncated"] = True
    return info


def _apply_fields(note: Note, fields: dict[str, str]) -> str | None:
    """Set fields by name. Returns a problem description, or None."""
    names = {name.lower(): name for name in note.keys()}  # noqa: SIM118  Note is not a dict
    unknown = [k for k in fields if str(k).lower() not in names]
    if unknown:
        return f"unknown field(s) {unknown}; the note type has {list(note.keys())}"
    for key, value in fields.items():
        note[names[str(key).lower()]] = to_html(str(value))
    return None


def _field_problem(state: int) -> str | None:
    if state == FieldCheck.EMPTY:
        return "the first field is empty"
    if state == FieldCheck.MISSING_CLOZE:
        return "a cloze note needs at least one {{c1::...}} deletion"
    if state == FieldCheck.NOTETYPE_NOT_CLOZE:
        return "the text has {{c1::...}} deletions but the note type is not a cloze type; use note_type 'Cloze'"
    return None


def _find_duplicate(col: Collection, note: Note) -> int | None:
    first = strip_html_media(note.fields[0])
    rows = col.db.all(
        "select id, flds from notes where csum = ? and mid = ? and id != ?",
        field_checksum(note.fields[0]),
        note.mid,
        note.id or 0,
    )
    for nid, flds in rows:
        if strip_html_media(flds.split("\x1f")[0]) == first:
            return nid
    return None


def add_notes(
    col: Collection,
    *,
    deck: str | int,
    notes: Sequence[dict[str, Any]],
    note_type: str | int = "Basic",
    tags: Sequence[str] | None = None,
    on_duplicate: str = "skip",
    create_deck: bool = True,
) -> dict[str, Any]:
    if on_duplicate not in ("skip", "allow", "update"):
        raise AnkiMcpError("on_duplicate must be 'skip', 'allow' or 'update'.")
    if not notes:
        raise AnkiMcpError("No notes given.")
    model = resolve_note_type(col, note_type)
    did, deck_name, deck_created = _deck_for_adding(col, deck, create_deck)
    if col.decks.is_filtered(did):
        raise AnkiMcpError(f"{deck_name!r} is a filtered deck; notes cannot be added to it.")
    common_tags = clean_tags(tags)

    requests: list[AddNoteRequest] = []
    updates: list[Note] = []
    problems: list[dict[str, Any]] = []
    skipped = 0
    seen: set[str] = set()

    for index, item in enumerate(notes):
        note = col.new_note(model)
        problem = _apply_fields(note, item.get("fields") or {})
        state = FieldCheck.NORMAL if problem else note.fields_check()
        problem = problem or _field_problem(state)
        if problem:
            problems.append({"index": index, "status": "failed", "reason": problem})
            continue
        note.tags = clean_tags([*common_tags, *(item.get("tags") or [])])

        key = strip_html_media(note.fields[0])
        duplicate_in_batch = key in seen
        seen.add(key)
        if on_duplicate != "allow" and (duplicate_in_batch or state == FieldCheck.DUPLICATE):
            existing = None if duplicate_in_batch else _find_duplicate(col, note)
            if on_duplicate == "update" and existing:
                old = col.get_note(existing)
                given = {str(k).lower() for k in item["fields"]}
                for name, value in note.items():
                    if name.lower() in given:
                        old[name] = value
                old.tags = clean_tags([*old.tags, *note.tags])
                updates.append(old)
                continue
            skipped += 1
            problems.append(
                {
                    "index": index,
                    "status": "skipped",
                    "reason": "duplicate of an earlier note in this call"
                    if duplicate_in_batch
                    else f"duplicate of note {existing}",
                    "first_field": _preview(note.fields[0]),
                }
            )
            continue
        requests.append(AddNoteRequest(note=note, deck_id=did))

    if requests:
        col.add_notes(requests)
    if updates:
        col.update_notes(updates)

    failed = sum(1 for p in problems if p["status"] == "failed")
    result: dict[str, Any] = {
        "deck": deck_name,
        "deck_id": did,
        "note_type": model["name"],
        "added": len(requests),
        "updated": len(updates),
        "skipped_duplicates": skipped,
        "failed": failed,
        "note_ids": [r.note.id for r in requests],
    }
    if deck_created:
        result["deck_created"] = True
    if updates:
        result["updated_note_ids"] = [n.id for n in updates]
    if problems:
        result["problems"] = problems
    return result


def find_notes(
    col: Collection, query: str, limit: int = 50, offset: int = 0, max_field_chars: int = 300
) -> dict[str, Any]:
    ids = col.find_notes(query, order="n.id desc")
    page = ids[max(0, offset) : max(0, offset) + max(1, limit)]
    notes = [_note_info(col, col.get_note(nid), max_field_chars or None) for nid in page]
    return {"total": len(ids), "offset": offset, "returned": len(notes), "notes": notes}


def get_notes(col: Collection, note_ids: Sequence[int]) -> dict[str, Any]:
    ids = [int(n) for n in note_ids]
    existing = set(col.db.list(f"select id from notes where id in ({_id_list(ids)})")) if ids else set()
    notes = [_note_info(col, col.get_note(nid)) for nid in ids if nid in existing]
    result: dict[str, Any] = {"notes": notes}
    missing = [nid for nid in ids if nid not in existing]
    if missing:
        result["not_found"] = missing
    return result


def update_notes(col: Collection, updates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not updates:
        raise AnkiMcpError("No updates given.")
    changed: list[Note] = []
    problems: list[dict[str, Any]] = []
    for index, item in enumerate(updates):
        nid = int(item["id"])
        if not col.db.scalar("select 1 from notes where id = ?", nid):
            problems.append({"index": index, "id": nid, "reason": "no note with this id"})
            continue
        note = col.get_note(nid)
        problem = _apply_fields(note, item.get("fields") or {})
        if not problem and note.fields_check() == FieldCheck.EMPTY:
            problem = "the first field would be empty"
        if problem:
            problems.append({"index": index, "id": nid, "reason": problem})
            continue
        if item.get("tags") is not None:
            note.tags = clean_tags(item["tags"])
        if item.get("add_tags"):
            note.tags = clean_tags([*note.tags, *item["add_tags"]])
        if item.get("remove_tags"):
            drop = {t.lower() for t in clean_tags(item["remove_tags"])}
            note.tags = [t for t in note.tags if t.lower() not in drop]
        changed.append(note)
    if changed:
        col.update_notes(changed)
    result: dict[str, Any] = {"updated": len(changed), "note_ids": [n.id for n in changed]}
    if problems:
        result["failed"] = len(problems)
        result["problems"] = problems
    return result


def delete_notes(col: Collection, note_ids: Sequence[int]) -> dict[str, Any]:
    ids = [int(n) for n in note_ids]
    if not ids:
        raise AnkiMcpError("No note ids given.")
    existing = col.db.list(f"select id from notes where id in ({_id_list(ids)})")
    cards = col.db.scalar(f"select count() from cards where nid in ({_id_list(existing)})") if existing else 0
    if existing:
        col.remove_notes(existing)
    result: dict[str, Any] = {"deleted_notes": len(existing), "deleted_cards": cards}
    missing = [nid for nid in ids if nid not in set(existing)]
    if missing:
        result["not_found"] = missing
    return result


# Cards
##########################################################################


def _card_info(col: Collection, card: Card, today: int) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": card.id,
        "note_id": card.nid,
        "deck": col.decks.name(card.odid or card.did),
        "card_type": card.template()["name"],
        "front": _preview(card.note().fields[col.models.sort_idx(card.note_type())]),
        "state": _CARD_STATES.get(card.type, str(card.type)),
    }
    if card.queue == QUEUE_TYPE_SUSPENDED:
        info["suspended"] = True
    elif card.queue in (QUEUE_TYPE_SIBLING_BURIED, QUEUE_TYPE_MANUALLY_BURIED):
        info["buried"] = True
    due = card.odue if card.odid else card.due
    if card.type == CARD_TYPE_NEW:
        info["new_position"] = due
    elif card.type == CARD_TYPE_REV or card.queue == QUEUE_TYPE_DAY_LEARN_RELEARN:
        local_today = datetime.datetime.now(tz=datetime.UTC).astimezone().date()
        info["due"] = (local_today + datetime.timedelta(days=due - today)).isoformat()
    elif due > 1_000_000_000:
        at = datetime.datetime.fromtimestamp(due, tz=datetime.UTC).astimezone()
        info["due"] = at.isoformat(timespec="minutes")
    if card.type != CARD_TYPE_NEW:
        info.update(
            interval_days=card.ivl,
            ease_percent=card.factor // 10,
            reviews=card.reps,
            lapses=card.lapses,
        )
    if card.user_flag():
        info["flag"] = card.user_flag()
    return info


def find_cards(col: Collection, query: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    ids = col.find_cards(query, order="c.id desc")
    page = ids[max(0, offset) : max(0, offset) + max(1, limit)]
    today = col.sched.today
    cards = [_card_info(col, col.get_card(cid), today) for cid in page]
    return {"total": len(ids), "offset": offset, "returned": len(cards), "cards": cards}


def move_cards(
    col: Collection,
    deck: str | int,
    card_ids: Sequence[int] | None = None,
    query: str | None = None,
    create_deck: bool = True,
) -> dict[str, Any]:
    ids = _select_cards(col, card_ids, query)
    did, name, created = _deck_for_adding(col, deck, create_deck)
    if col.decks.is_filtered(did):
        raise AnkiMcpError(f"{name!r} is a filtered deck; cards cannot be moved into it.")
    if ids:
        col.set_deck(ids, did)
    result: dict[str, Any] = {"moved": len(ids), "deck": name, "deck_id": did}
    if created:
        result["deck_created"] = True
    return result


def set_suspended(
    col: Collection,
    suspended: bool,
    card_ids: Sequence[int] | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    ids = _select_cards(col, card_ids, query)
    if ids:
        if suspended:
            col.sched.suspend_cards(ids)
        else:
            col.sched.unsuspend_cards(ids)
    return {"suspended" if suspended else "unsuspended": len(ids)}


# Tags
##########################################################################


def list_tags(col: Collection) -> dict[str, Any]:
    tags = col.tags.all()
    return {"count": len(tags), "tags": tags}


def update_tags(
    col: Collection,
    note_ids: Sequence[int] | None = None,
    query: str | None = None,
    add: Sequence[str] | None = None,
    remove: Sequence[str] | None = None,
) -> dict[str, Any]:
    add_tags, remove_tags = clean_tags(add), clean_tags(remove)
    if not add_tags and not remove_tags:
        raise AnkiMcpError("Pass tags to add, tags to remove, or both.")
    ids = _select_notes(col, note_ids, query)
    result: dict[str, Any] = {"notes": len(ids)}
    if ids and add_tags:
        result["added"] = add_tags
        col.tags.bulk_add(ids, " ".join(add_tags))
    if ids and remove_tags:
        result["removed"] = remove_tags
        col.tags.bulk_remove(ids, " ".join(remove_tags))
    return result


# Media
##########################################################################


def store_media(
    col: Collection, filename: str | None = None, path: str | None = None, data_base64: str | None = None
) -> dict[str, Any]:
    if bool(path) == bool(data_base64):
        raise AnkiMcpError("Pass either path or data_base64.")
    if path:
        path = _expand(path)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise AnkiMcpError(f"Cannot read {path}: {e.strerror}") from None
        filename = filename or os.path.basename(path)
    else:
        if not filename:
            raise AnkiMcpError("data_base64 needs a filename.")
        try:
            data = base64.b64decode(data_base64 or "", validate=True)
        except (binascii.Error, ValueError):
            raise AnkiMcpError("data_base64 is not valid base64.") from None
    # Anki picks another name if a different file with this name exists.
    stored = col.media.write_data(os.path.basename(filename), data)
    audio = stored.lower().rsplit(".", 1)[-1] in ("mp3", "ogg", "wav", "m4a", "flac", "opus", "mp4", "webm")
    return {
        "filename": stored,
        "bytes": len(data),
        "use_in_field": f"[sound:{stored}]" if audio else f'<img src="{stored}">',
    }


# Packages
##########################################################################


def import_package(
    col: Collection, path: str, update_notes: str = "if_newer", with_scheduling: bool = False
) -> dict[str, Any]:
    if update_notes not in _UPDATE_CONDITIONS:
        raise AnkiMcpError("update_notes must be 'if_newer', 'always' or 'never'.")
    path = _expand(path)
    if not os.path.isfile(path):
        raise AnkiMcpError(f"There is no file at {path}.")
    decks_before = {d.id for d in col.decks.all_names_and_ids()}
    condition = _UPDATE_CONDITIONS[update_notes]
    # merge_notetypes stays off: merging changes existing note types in a way
    # that forces a full sync.
    options = ImportAnkiPackageOptions(
        merge_notetypes=False,
        update_notes=condition,
        update_notetypes=condition,
        with_scheduling=with_scheduling,
        with_deck_configs=False,
    )
    log = col.import_anki_package(ImportAnkiPackageRequest(package_path=path, options=options)).log
    result: dict[str, Any] = {
        "file": path,
        "notes_in_package": log.found_notes,
        "added": len(log.new),
        "updated": len(log.updated),
        "unchanged_duplicates": len(log.duplicate),
        "conflicting": len(log.conflicting),
        "new_decks": [d.name for d in col.decks.all_names_and_ids() if d.id not in decks_before],
    }
    if log.conflicting:
        result["conflict_detail"] = (
            "These notes exist with a note type whose fields or templates differ, so they were left alone."
        )
    return result


def export_package(
    col: Collection,
    deck: str | int,
    path: str,
    with_scheduling: bool = False,
    with_media: bool = True,
    legacy: bool = False,
) -> dict[str, Any]:
    did, name = resolve_deck(col, deck)
    path = _expand(path)
    if not path.lower().endswith(".apkg"):
        path += ".apkg"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    notes = col.export_anki_package(
        out_path=path,
        options=ExportAnkiPackageOptions(
            with_scheduling=with_scheduling,
            with_deck_configs=False,
            with_media=with_media,
            legacy=legacy,
        ),
        limit=DeckIdLimit(did),
    )
    return {"file": path, "deck": name, "notes": notes}
