"""Settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MEDIA_MODES = ("auto", "always", "never")


class ConfigError(Exception):
    pass


def _default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join("~", ".local", "share")
    return Path(base).expanduser() / "anki-mcp"


def _seconds(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        raise ConfigError(f"{name} must be a number of seconds, got {raw!r}") from None


@dataclass(frozen=True)
class Config:
    data_dir: Path
    username: str | None
    password: str | None
    # Base URL of a self-hosted sync server. None means AnkiWeb.
    endpoint: str | None
    # auto: sync media only after this server added files. always: with every
    # sync. never: leave media alone.
    sync_media: str
    # Reads skip the sync if the last one is younger than this.
    read_sync_ttl: int
    media_timeout: int
    lock_timeout: int

    @property
    def collection_path(self) -> Path:
        return self.data_dir / "collection.anki2"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "collection.media"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "lock"

    @property
    def server_name(self) -> str:
        return self.endpoint or "AnkiWeb"

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ
        data_dir = env.get("ANKI_MCP_DATA_DIR", "").strip()
        endpoint = env.get("ANKI_SYNC_ENDPOINT", "").strip() or None
        if endpoint and not endpoint.endswith("/"):
            endpoint += "/"
        media = env.get("ANKI_MCP_SYNC_MEDIA", "auto").strip().lower() or "auto"
        if media not in MEDIA_MODES:
            raise ConfigError(
                f"ANKI_MCP_SYNC_MEDIA must be one of {', '.join(MEDIA_MODES)}, got {media!r}"
            )
        return cls(
            data_dir=Path(data_dir).expanduser() if data_dir else _default_data_dir(),
            username=env.get("ANKIWEB_USERNAME", "").strip() or None,
            password=env.get("ANKIWEB_PASSWORD") or None,
            endpoint=endpoint,
            sync_media=media,
            read_sync_ttl=_seconds("ANKI_MCP_READ_SYNC_TTL", 60),
            media_timeout=_seconds("ANKI_MCP_MEDIA_TIMEOUT", 300),
            lock_timeout=_seconds("ANKI_MCP_LOCK_TIMEOUT", 60),
        )
