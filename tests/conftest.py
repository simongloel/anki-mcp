"""Fixtures that run Anki's own sync server, so tests cover the real protocol."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from anki.collection import Collection

import anki_mcp  # noqa: F401  loads anki.collection first
from anki_mcp.config import Config
from anki_mcp.session import Session

USER = "student@example.com"
PASSWORD = "correct horse"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sync_server(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    env = {
        **os.environ,
        "SYNC_USER1": f"{USER}:{PASSWORD}",
        "SYNC_USER2": f"other@example.com:{PASSWORD}",
        "SYNC_BASE": str(tmp_path / "syncserver"),
        "SYNC_HOST": "127.0.0.1",
        "SYNC_PORT": str(port),
        "RUST_LOG": "error",
    }
    proc = subprocess.Popen([sys.executable, "-m", "anki.syncserver"], env=env)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("the sync server did not start") from None
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}/"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def make_config(data_dir: Path, endpoint: str | None, **overrides: object) -> Config:
    values: dict[str, object] = {
        "data_dir": data_dir,
        "username": USER,
        "password": PASSWORD,
        "endpoint": endpoint,
        "sync_media": "auto",
        # Tests switch between devices, so reads must always look at the server.
        "read_sync_ttl": 0,
        "media_timeout": 30,
        "lock_timeout": 5,
    }
    values.update(overrides)
    return Config(**values)  # type: ignore[arg-type]


@pytest.fixture
def device(tmp_path: Path, sync_server: str) -> Callable[..., Session]:
    """Builds sessions that act like separate Anki devices on one account."""

    def build(name: str, **overrides: object) -> Session:
        return Session(make_config(tmp_path / name, sync_server, **overrides))

    return build


@pytest.fixture
def col(tmp_path: Path) -> Iterator[Collection]:
    """A local collection with no account, for tests that never sync."""
    (tmp_path / "offline").mkdir()
    collection = Collection(str(tmp_path / "offline" / "collection.anki2"))
    try:
        yield collection
    finally:
        collection.close()
