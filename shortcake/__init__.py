"""Shortcake CLI package."""

import sys
from pathlib import Path

__version__ = "0.1.0"


def get_cli_name() -> str:
    """Get the CLI name based on how it was invoked (sc or shortcake)."""
    if sys.argv:
        name = Path(sys.argv[0]).name
        if name in ("sc", "shortcake"):
            return name
    return "shortcake"
