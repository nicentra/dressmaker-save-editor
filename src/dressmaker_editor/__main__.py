"""``python -m dressmaker_editor`` and the packaged executable start here."""

from __future__ import annotations

import sys


def main() -> int:
    from dressmaker_editor.gui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
