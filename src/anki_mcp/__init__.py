"""An MCP server that edits an Anki collection and syncs it with AnkiWeb."""

# anki's modules import each other in a circle that only resolves when
# anki.collection is loaded first.
import anki.collection
import anki.lang

# anki.utils needs a translation backend for its HTML helpers. English also
# keeps error messages in one language whatever the system locale is.
anki.lang.set_lang("en")

__version__ = "0.1.0"


def main() -> None:
    from .cli import main as cli_main

    cli_main()
