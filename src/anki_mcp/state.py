"""The sign-in state kept between runs.

Like Anki desktop, the server keeps the host key AnkiWeb hands out at login
and never writes the password to disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class State:
    username: str | None = None
    hkey: str | None = None
    # The endpoint the user logged in against (None for AnkiWeb).
    login_endpoint: str | None = None
    # The shard AnkiWeb redirected us to, e.g. https://sync5.ankiweb.net/.
    endpoint: str | None = None
    last_sync: float = 0.0
    # Media files were added locally and have not been uploaded yet.
    media_pending: bool = False

    @classmethod
    def load(cls, path: Path) -> State:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, path)

    def forget_login(self) -> None:
        self.hkey = None
        self.endpoint = None
