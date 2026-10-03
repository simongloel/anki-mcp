"""Opens the local collection, signs in and keeps it in sync.

The server owns a headless collection in its data directory, the same way
Anki desktop or AnkiDroid own theirs. Every call opens it, syncs, does its
work, syncs again and closes it. All of that goes through Anki's own Rust
backend, so the wire protocol and the merge rules are the ones the official
clients use.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

from anki._backend import RustBackend
from anki.collection import Collection
from anki.errors import AnkiException, SyncError, SyncErrorKind
from anki.sync import SyncAuth, SyncOutput

from .config import Config
from .state import State

T = TypeVar("T")


class AnkiMcpError(Exception):
    """An error whose message is meant for the user."""


class NotSignedIn(AnkiMcpError):
    pass


class SyncFailed(AnkiMcpError):
    pass


class FullSyncRequired(AnkiMcpError):
    """A normal sync is not possible; one side has to replace the other."""

    def __init__(self, message: str, *, remote_empty: bool):
        super().__init__(message)
        self.remote_empty = remote_empty


SIGN_IN_HINT = (
    "Not signed in. Set ANKIWEB_USERNAME and ANKIWEB_PASSWORD in the server's "
    "environment, or run `anki-mcp login` in a terminal."
)


@contextlib.contextmanager
def _file_lock(path: os.PathLike[str], timeout: float) -> Iterator[None]:
    """Only one process may have the collection open at a time."""
    with open(path, "a+b") as f:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock(f)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise AnkiMcpError(
                        "Another anki-mcp process is using the collection. Try again in a moment."
                    ) from None
                time.sleep(0.1)
        try:
            yield
        finally:
            _unlock(f)


if sys.platform == "win32":
    import msvcrt

    def _lock(f: Any) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f: Any) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(f: Any) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f: Any) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class Session:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._thread_lock = threading.Lock()
        self._backend: RustBackend | None = None
        self.state = State()

    # Opening
    ######################################################################

    @contextlib.contextmanager
    def _open(self, username: str | None = None) -> Iterator[Collection]:
        cfg = self.cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._thread_lock, _file_lock(cfg.lock_path, cfg.lock_timeout):
            # Another process may have signed in or synced since our last call.
            self.state = State.load(cfg.state_path)
            self._archive_foreign_collection(username or cfg.username)
            if self._backend is None:
                self._backend = RustBackend()
            col = Collection(str(cfg.collection_path), backend=self._backend)
            try:
                yield col
            finally:
                col.close()

    def _archive_foreign_collection(self, username: str | None) -> None:
        """Set the collection aside if it belongs to another account or server."""
        cfg, st = self.cfg, self.state
        if not st.username:
            return
        other_user = username and username.lower() != st.username.lower()
        other_server = st.login_endpoint != cfg.endpoint
        if not (other_user or other_server):
            return
        files = [
            p
            for p in cfg.data_dir.iterdir()
            if p.name.startswith("collection.") or p.name == "backups"
        ]
        if files:
            target = cfg.data_dir / "archive" / f"{_safe(st.username)}-{int(time.time())}"
            target.mkdir(parents=True)
            for p in files:
                shutil.move(str(p), str(target / p.name))
        self.state = State()
        self._save_state()

    def _save_state(self) -> None:
        self.state.save(self.cfg.state_path)

    # Signing in
    ######################################################################

    def _login(self, col: Collection, username: str | None = None, password: str | None = None) -> None:
        cfg, st = self.cfg, self.state
        username = username or cfg.username
        password = password or cfg.password
        if not username or not password:
            raise NotSignedIn(SIGN_IN_HINT)
        try:
            auth = col.sync_login(username, password, cfg.endpoint)
        except SyncError as e:
            if e.kind is SyncErrorKind.AUTH:
                raise NotSignedIn(
                    f"{cfg.server_name} rejected the username or password for {username}."
                ) from None
            raise SyncFailed(f"Could not sign in to {cfg.server_name}: {e}") from None
        except AnkiException as e:
            raise SyncFailed(f"Could not reach {cfg.server_name}: {e}") from None
        st.username = username
        st.hkey = auth.hkey
        st.login_endpoint = cfg.endpoint
        st.endpoint = None
        self._save_state()

    def _auth(self, col: Collection) -> SyncAuth:
        if not self.state.hkey:
            self._login(col)
        return SyncAuth(hkey=self.state.hkey, endpoint=self.state.endpoint or self.cfg.endpoint)

    def _with_auth(self, col: Collection, call: Callable[[SyncAuth], T]) -> T:
        """Run a sync call, signing in again once if the host key has expired."""
        for attempt in (1, 2):
            auth = self._auth(col)
            try:
                return call(auth)
            except SyncError as e:
                if e.kind is not SyncErrorKind.AUTH:
                    raise SyncFailed(f"Sync with {self.cfg.server_name} failed: {e}") from None
                self.state.forget_login()
                self._save_state()
                if attempt == 2 or not (self.cfg.username and self.cfg.password):
                    raise NotSignedIn(
                        f"The saved sign-in is no longer valid. {SIGN_IN_HINT}"
                    ) from None
            except AnkiException as e:
                raise SyncFailed(f"Sync with {self.cfg.server_name} failed: {e}") from None
        raise AssertionError("unreachable")

    # Syncing
    ######################################################################

    def _sync(self, col: Collection, *, media: bool) -> dict[str, Any]:
        """Bring the local collection and the server in line.

        Raises FullSyncRequired when that needs a one-way sync the user has
        to pick.
        """
        out: SyncOutput = self._with_auth(col, lambda auth: col.sync_collection(auth, False))
        if out.new_endpoint:
            self.state.endpoint = out.new_endpoint
            self._save_state()
        info: dict[str, Any] = {"status": "synced"}
        if out.server_message:
            info["server_message"] = out.server_message

        if out.required != SyncOutput.NO_CHANGES:
            mod, ls = col.db.first("select mod, ls from col")
            unsynced_changes = mod > ls
            if out.required == SyncOutput.FULL_DOWNLOAD or (
                out.required == SyncOutput.FULL_SYNC and not unsynced_changes
            ):
                # Nothing local to lose: take the server's collection.
                self._full_sync(col, upload=False)
                info["full_download"] = True
            else:
                # FULL_SYNC with no local cards only happens if the server
                # has none either.
                remote_empty = out.required == SyncOutput.FULL_UPLOAD or col.is_empty()
                raise FullSyncRequired(_full_sync_message(self.cfg, remote_empty), remote_empty=remote_empty)

        self.state.last_sync = time.time()
        self._save_state()
        if media and self._wants_media_sync():
            info["media"] = self._sync_media(col)
        return info

    def _full_sync(self, col: Collection, *, upload: bool) -> None:
        if not upload and not col.is_empty():
            self._backup(col, force=True)
        auth = self._auth(col)
        col.close_for_full_sync()
        try:
            col.full_upload_or_download(auth=auth, server_usn=None, upload=upload)
        except SyncError as e:
            if e.kind is SyncErrorKind.AUTH:
                self.state.forget_login()
                self._save_state()
                raise NotSignedIn(f"The saved sign-in is no longer valid. {SIGN_IN_HINT}") from None
            raise SyncFailed(f"Full sync with {self.cfg.server_name} failed: {e}") from None
        except AnkiException as e:
            raise SyncFailed(f"Full sync with {self.cfg.server_name} failed: {e}") from None
        finally:
            # The backend reopens the collection whatever the outcome.
            col.reopen(after_full_sync=True)

    def _wants_media_sync(self) -> bool:
        mode = self.cfg.sync_media
        return mode == "always" or (mode == "auto" and self.state.media_pending)

    def _sync_media(self, col: Collection) -> dict[str, Any]:
        """Run Anki's background media sync and wait for it."""
        try:
            col.sync_media(self._auth(col))
            deadline = time.monotonic() + self.cfg.media_timeout
            while True:
                status = col.media_sync_status()
                if not status.active:
                    break
                if time.monotonic() >= deadline:
                    col.abort_media_sync()
                    return {
                        "status": "incomplete",
                        "detail": "Media sync ran out of time and continues with the next sync.",
                    }
                time.sleep(0.2)
        except AnkiException as e:
            return {"status": "failed", "error": str(e)}
        self.state.media_pending = False
        self._save_state()
        return {"status": "synced"}

    def _backup(self, col: Collection, *, force: bool = False) -> None:
        # Anki throttles this itself: without force it writes at most one
        # backup per backup interval, and only if something changed.
        try:
            self.cfg.backup_dir.mkdir(exist_ok=True)
            col.create_backup(
                backup_folder=str(self.cfg.backup_dir), force=force, wait_for_completion=True
            )
        except (AnkiException, OSError) as e:
            print(f"anki-mcp: backup failed: {e}", file=sys.stderr)

    # Entry points
    ######################################################################

    def read(self, fn: Callable[[Collection], T]) -> tuple[T, dict[str, Any]]:
        """Run fn on a collection that is at most read_sync_ttl seconds old."""
        with self._open() as col:
            if time.time() - self.state.last_sync < self.cfg.read_sync_ttl:
                sync = {"status": "fresh"}
            else:
                try:
                    sync = self._sync(col, media=False)
                except AnkiMcpError as e:
                    # Stale local data beats no answer, but only if there is any.
                    if not self.state.last_sync:
                        raise
                    sync = _sync_problem(e)
            return fn(col), sync

    def write(self, fn: Callable[[Collection], T], *, adds_media: bool = False) -> tuple[T, dict[str, Any]]:
        """Sync, run fn, and sync again so the change reaches the server."""
        with self._open() as col:
            try:
                self._sync(col, media=False)
            except FullSyncRequired as e:
                # With an empty server the local collection is the only copy,
                # so editing it cannot make things diverge.
                if not e.remote_empty:
                    raise
            self._backup(col)
            media_before = _listing(self.cfg.media_dir) if adds_media else None
            result = fn(col)
            if adds_media and _listing(self.cfg.media_dir) != media_before:
                self.state.media_pending = True
                self._save_state()
            try:
                sync = self._sync(col, media=True)
            except AnkiMcpError as e:
                sync = _sync_problem(e)
                sync["detail"] = "The change is saved locally and goes out with the next successful sync."
            return result, sync

    def sync(self, *, media: bool | None = None) -> dict[str, Any]:
        with self._open() as col:
            info = self._sync(col, media=False)
            if media or (media is None and self._wants_media_sync()):
                info["media"] = self._sync_media(col)
            info.update(_counts(col))
            return info

    def full_sync(self, direction: str) -> dict[str, Any]:
        if direction not in ("upload", "download"):
            raise AnkiMcpError("direction must be 'upload' or 'download'")
        upload = direction == "upload"
        with self._open() as col:
            out: SyncOutput = self._with_auth(col, lambda auth: col.sync_collection(auth, False))
            if out.new_endpoint:
                self.state.endpoint = out.new_endpoint
                self._save_state()
            if out.required == SyncOutput.NO_CHANGES:
                self.state.last_sync = time.time()
                self._save_state()
                return {"status": "synced", "detail": "Already in sync, no full sync needed.", **_counts(col)}
            if upload and out.required == SyncOutput.FULL_DOWNLOAD:
                raise AnkiMcpError(
                    "The local collection has no cards, so uploading it would wipe the "
                    f"collection on {self.cfg.server_name}. Use direction='download'."
                )
            if not upload and out.required == SyncOutput.FULL_UPLOAD:
                raise AnkiMcpError(
                    f"The collection on {self.cfg.server_name} has no cards, so downloading it "
                    "would wipe the local one. Use direction='upload'."
                )
            self._full_sync(col, upload=upload)
            self.state.last_sync = time.time()
            self._save_state()
            info: dict[str, Any] = {"status": "synced", "full_sync": direction}
            if self._wants_media_sync():
                info["media"] = self._sync_media(col)
            info.update(_counts(col))
            return info

    def status(self) -> dict[str, Any]:
        """What is known without touching the network."""
        with self._open() as col:
            st, cfg = self.state, self.cfg
            mod, ls = col.db.first("select mod, ls from col")
            return {
                "signed_in": bool(st.hkey),
                "username": st.username or cfg.username,
                "credentials_in_environment": bool(cfg.username and cfg.password),
                "server": st.endpoint or cfg.server_name,
                "data_dir": str(cfg.data_dir),
                "last_sync": _iso(st.last_sync),
                "unsynced_changes": bool(mod > ls),
                "media_sync": cfg.sync_media,
                "media_pending": st.media_pending,
                **_counts(col),
            }

    def login(self, username: str | None = None, password: str | None = None) -> dict[str, Any]:
        """Sign in with the given or configured credentials and run a first sync."""
        with self._open(username) as col:
            self._login(col, username, password)
            info = self._sync(col, media=False)
            info.update(_counts(col))
            info["username"] = self.state.username
            return info

    def logout(self) -> None:
        with self._open():
            self.state.forget_login()
            self._save_state()


def _full_sync_message(cfg: Config, remote_empty: bool) -> str:
    if remote_empty:
        return (
            f"The collection on {cfg.server_name} is empty, so the first sync has to upload the "
            "local collection in one piece. Ask the user, then call full_sync with "
            "direction='upload' and confirm=true."
        )
    return (
        f"The local collection and {cfg.server_name} have diverged and cannot be merged (one side "
        "changed a note type or was restored from a backup). One side has to replace the other: "
        "full_sync with direction='download' drops the local changes that were not synced yet, "
        "direction='upload' overwrites the server and forces every other device to download. "
        "Ask the user which one they want."
    )


def _sync_problem(e: AnkiMcpError) -> dict[str, Any]:
    if isinstance(e, FullSyncRequired):
        status = "full_sync_required"
    elif isinstance(e, NotSignedIn):
        status = "not_signed_in"
    else:
        status = "failed"
    return {"status": status, "error": str(e)}


def _counts(col: Collection) -> dict[str, int]:
    return {"notes": col.note_count(), "cards": col.card_count(), "decks": col.decks.count()}


def _listing(path: os.PathLike[str]) -> set[str]:
    try:
        return set(os.listdir(path))
    except OSError:
        return set()


def _iso(ts: float) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
