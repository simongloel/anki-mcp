"""Command line: run the server, sign in, check the setup."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from . import __version__
from .config import Config, ConfigError
from .session import AnkiMcpError, Session


def _print(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _serve(cfg: Config, _args: argparse.Namespace) -> None:
    from .server import build_server

    build_server(cfg).run("stdio")


def _login(cfg: Config, args: argparse.Namespace) -> None:
    username = args.username or cfg.username or input(f"{cfg.server_name} email: ").strip()
    password = cfg.password if cfg.password and username == cfg.username else None
    if not password:
        password = getpass.getpass(f"Password for {username}: ")
    info = Session(cfg).login(username, password)
    print(
        f"Signed in as {info['username']}. The collection has {info['notes']} notes "
        f"in {info['decks']} decks.\nOnly the session key is stored, in {cfg.state_path}."
    )


def _logout(cfg: Config, _args: argparse.Namespace) -> None:
    Session(cfg).logout()
    print("Signed out. The local copy of the collection is still in", cfg.data_dir)


def _status(cfg: Config, _args: argparse.Namespace) -> None:
    _print(Session(cfg).status())


def _sync(cfg: Config, args: argparse.Namespace) -> None:
    _print(Session(cfg).sync(media=True if args.media else None))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="anki-mcp",
        description="MCP server that edits an Anki collection and syncs it with AnkiWeb.",
    )
    parser.add_argument("--version", action="version", version=f"anki-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (the default)").set_defaults(run=_serve)
    login = sub.add_parser("login", help="sign in and store the session key, so no password has to be configured")
    login.add_argument("--username", help="AnkiWeb email address")
    login.set_defaults(run=_login)
    sub.add_parser("logout", help="forget the session key").set_defaults(run=_logout)
    sub.add_parser("status", help="show the sign-in and sync state").set_defaults(run=_status)
    sync = sub.add_parser("sync", help="sync now and show the result")
    sync.add_argument("--media", action="store_true", help="sync media files too")
    sync.set_defaults(run=_sync)

    args = parser.parse_args(argv)
    run = getattr(args, "run", _serve)
    try:
        run(Config.from_env(), args)
    except (AnkiMcpError, ConfigError) as e:
        print(f"anki-mcp: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
