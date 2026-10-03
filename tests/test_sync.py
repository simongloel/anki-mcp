"""End-to-end against Anki's sync server: login, sync, full sync, media."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from anki.collection import Collection

from anki_mcp import ops
from anki_mcp.session import FullSyncRequired, NotSignedIn, Session, SyncFailed
from anki_mcp.state import State

from .conftest import PASSWORD, _free_port

Device = Callable[..., Session]


def add(front: str, deck: str = "Deck") -> Callable[[Collection], dict]:
    return lambda col: ops.add_notes(col, deck=deck, notes=[{"fields": {"Front": front, "Back": "b"}}])


def fronts(session: Session) -> list[str]:
    result, sync = session.read(lambda col: ops.find_notes(col, ""))
    assert sync["status"] == "synced"
    return sorted(n["fields"]["Front"] for n in result["notes"])


def seeded(device: Device, name: str = "laptop") -> Session:
    """A device whose collection is on the server, as with an existing account."""
    session = device(name)
    session.write(add("seed"))
    assert session.full_sync("upload")["status"] == "synced"
    return session


def dead_endpoint() -> str:
    return f"http://127.0.0.1:{_free_port()}/"


def change_schema(session: Session) -> None:
    """Adding a field is one of the edits Anki can only sync one way."""
    with session._open() as col:
        model = col.models.by_name("Basic")
        col.models.add_field(model, col.models.new_field("Extra"))
        col.models.update_dict(model)


def test_new_account_needs_one_upload_then_syncs_normally(device: Device) -> None:
    laptop = device("laptop")
    result, sync = laptop.write(add("first"))
    assert result["added"] == 1
    assert sync["status"] == "full_sync_required" and "direction='upload'" in sync["error"]

    # further edits are not blocked while the server is still empty
    _, sync = laptop.write(add("second"))
    assert sync["status"] == "full_sync_required"

    with pytest.raises(Exception, match="would wipe the local one"):
        laptop.full_sync("download")
    assert laptop.full_sync("upload")["notes"] == 2

    _, sync = laptop.write(add("third"))
    assert sync == {"status": "synced"}
    assert laptop.status()["unsynced_changes"] is False


def test_second_device_downloads_and_edits_travel_both_ways(device: Device) -> None:
    laptop = seeded(device)
    phone = device("phone")
    assert phone.sync()["full_download"] is True
    assert fronts(phone) == ["seed"]

    phone.write(add("from phone", deck="Phone::Sub"))
    assert fronts(laptop) == ["from phone", "seed"]

    nid = laptop.read(lambda col: ops.find_notes(col, "front:seed"))[0]["notes"][0]["id"]
    _, sync = laptop.write(lambda col: ops.update_notes(col, [{"id": nid, "fields": {"Front": "edited"}}]))
    assert sync == {"status": "synced"}
    assert fronts(phone) == ["edited", "from phone"]

    phone.write(lambda col: ops.delete_notes(col, [nid]))
    phone.write(lambda col: ops.delete_deck(col, "Deck"))
    decks, _ = laptop.read(ops.list_decks)
    assert fronts(laptop) == ["from phone"]
    assert "Deck" not in [d["name"] for d in decks["decks"]]


def test_only_the_host_key_is_stored(device: Device) -> None:
    laptop = seeded(device)
    raw = laptop.cfg.state_path.read_text()
    assert PASSWORD not in raw
    assert json.loads(raw)["hkey"]
    assert oct(laptop.cfg.state_path.stat().st_mode & 0o777) == "0o600"

    # a later run needs no password
    relaunched = device("laptop", password=None)
    assert relaunched.sync()["status"] == "synced"


def test_wrong_password_and_missing_credentials(device: Device) -> None:
    with pytest.raises(NotSignedIn, match="rejected the username or password"):
        device("a", password="wrong").sync()
    with pytest.raises(NotSignedIn, match="anki-mcp login"):
        device("b", username=None, password=None).write(add("x"))
    # nothing was written while signed out
    assert device("b", username=None, password=None).status()["notes"] == 0


def test_expired_host_key_signs_in_again(device: Device) -> None:
    laptop = seeded(device)
    state = State.load(laptop.cfg.state_path)
    state.hkey = "expired"
    state.save(laptop.cfg.state_path)
    assert laptop.sync()["status"] == "synced"
    assert State.load(laptop.cfg.state_path).hkey != "expired"

    # without credentials in the environment the user has to log in again
    state.save(laptop.cfg.state_path)
    with pytest.raises(NotSignedIn, match="no longer valid"):
        device("laptop", password=None).sync()


def test_login_with_explicit_credentials(device: Device) -> None:
    seeded(device)
    cli = device("cli", username=None, password=None)
    info = cli.login("student@example.com", PASSWORD)
    assert info["notes"] == 1 and info["username"] == "student@example.com"
    cli.logout()
    assert cli.status()["signed_in"] is False


def test_writes_are_refused_while_the_server_is_unreachable(device: Device) -> None:
    laptop = seeded(device)
    state = State.load(laptop.cfg.state_path)
    state.endpoint = dead_endpoint()
    state.save(laptop.cfg.state_path)
    with pytest.raises(SyncFailed):
        laptop.write(add("x"))
    # the edit was refused, so nothing can diverge
    assert laptop.status()["notes"] == 1


def test_read_falls_back_to_local_copy_when_sync_fails(device: Device) -> None:
    laptop = seeded(device)
    state = State.load(laptop.cfg.state_path)
    state.endpoint = dead_endpoint()
    state.save(laptop.cfg.state_path)
    result, sync = laptop.read(lambda col: ops.find_notes(col, ""))
    assert result["total"] == 1
    assert sync["status"] == "failed"


def test_schema_change_elsewhere_downloads_when_nothing_local_is_pending(device: Device) -> None:
    laptop = seeded(device)
    phone = device("phone")
    phone.sync()

    # the phone changes a note type and uploads, as Anki desktop would
    change_schema(phone)
    assert phone.full_sync("upload")["full_sync"] == "upload"
    phone.write(add("after schema change"))

    _, sync = laptop.write(add("laptop continues"))
    assert sync == {"status": "synced"}
    assert fronts(laptop) == ["after schema change", "laptop continues", "seed"]
    assert any(laptop.cfg.backup_dir.iterdir()), "a backup is kept before the local copy is replaced"


def test_diverged_collections_need_a_decision(device: Device) -> None:
    laptop = seeded(device)
    phone = device("phone")
    phone.sync()
    change_schema(phone)
    phone.full_sync("upload")

    # the laptop has a change that never reached the server
    with laptop._open() as col:
        add("unsynced")(col)

    with pytest.raises(FullSyncRequired, match="diverged") as e:
        laptop.write(add("blocked"))
    assert e.value.remote_empty is False
    result, sync = laptop.read(lambda col: ops.find_notes(col, ""))
    assert sync["status"] == "full_sync_required" and result["total"] == 2

    assert laptop.full_sync("download")["notes"] == 1
    assert fronts(laptop) == ["seed"]


def test_switching_accounts_archives_the_old_collection(device: Device) -> None:
    laptop = seeded(device)
    other = device("laptop", username="other@example.com")
    assert other.status()["notes"] == 0
    archive = laptop.cfg.data_dir / "archive"
    (kept,) = archive.iterdir()
    assert kept.name.startswith("student_example.com-")
    assert (kept / "collection.anki2").exists()
    _, sync = other.write(add("other account"))
    assert sync["status"] == "full_sync_required"  # that account is new and empty


def test_media_reaches_other_devices(device: Device, tmp_path: Path) -> None:
    laptop = seeded(device)
    image = tmp_path / "plot.png"
    image.write_bytes(b"\x89PNG not really")

    stored, sync = laptop.write(lambda col: ops.store_media(col, path=str(image)), adds_media=True)
    assert sync["media"] == {"status": "synced"}
    assert laptop.status()["media_pending"] is False

    phone = device("phone")
    info = phone.sync(media=True)
    assert info["media"] == {"status": "synced"}
    assert (phone.cfg.media_dir / stored["filename"]).read_bytes() == b"\x89PNG not really"

    # plain edits leave media alone
    _, sync = laptop.write(add("no media"))
    assert "media" not in sync


def test_media_mode_never(device: Device, tmp_path: Path) -> None:
    laptop = seeded(device, "quiet")
    quiet = device("quiet", sync_media="never")
    _, sync = quiet.write(
        lambda col: ops.store_media(col, filename="a.png", data_base64="eA=="), adds_media=True
    )
    assert "media" not in sync and quiet.status()["media_pending"] is True
    assert laptop.cfg.data_dir == quiet.cfg.data_dir


def test_imported_package_syncs(device: Device, tmp_path: Path) -> None:
    laptop = seeded(device)
    package = str(tmp_path / "deck.apkg")
    laptop.write(add("shared", deck="Shared"))
    laptop.read(lambda col: ops.export_package(col, "Shared", package))
    laptop.write(lambda col: ops.delete_deck(col, "Shared", delete_cards=True))

    phone = device("phone")
    result, sync = phone.write(lambda col: ops.import_package(col, package), adds_media=True)
    assert result["added"] == 1 and sync == {"status": "synced"}
    assert fronts(laptop) == ["seed", "shared"]


def test_two_processes_share_the_collection(device: Device) -> None:
    """Two Claude sessions each start their own server process."""
    seeded(device)
    first, second = device("laptop"), device("laptop")
    first.write(add("one"))
    second.write(add("two"))
    assert fronts(first) == ["one", "seed", "two"]
    assert os.path.exists(first.cfg.lock_path)
