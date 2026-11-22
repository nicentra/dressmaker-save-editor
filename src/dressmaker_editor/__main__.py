"""``python -m dressmaker_editor`` and the packaged executable start here.

``--self-test <report>`` checks that everything the editor needs made it into
the build (data files, native libraries, Qt) and exits with 0 or 1. CI runs
it against the packaged executable, which has no console to print to.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path


def self_test(report: Path) -> int:
    lines: list[str] = []
    code = 0
    try:
        from dressmaker_editor import full_version
        from dressmaker_editor.catalog import Catalog
        from dressmaker_editor.story import bundled_template_dress

        catalog = Catalog.bundled()
        lines.append(f"version {full_version()}")
        lines.append(
            f"catalog: {len(catalog.assets)} assets, {len(catalog.main_quests)} main quests"
        )
        bundled_template_dress()
        lines.append("placeholder dress: ok")

        import dnfile  # noqa: F401  (needed to read the game's DLLs)
        import psutil
        import UnityPy
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

        TypeTreeGenerator(catalog.source.get("unity_version") or "6000.3.4f1")
        lines.append(f"UnityPy {UnityPy.__version__}, type tree generator: ok")
        lines.append(f"psutil {psutil.__version__}")

        from dressmaker_editor.extract import extract_catalog
        from dressmaker_editor.paths import find_game_install

        install = find_game_install()
        if install is None:
            lines.append("game: not installed, extraction skipped")
        else:
            extracted = extract_catalog(install)
            lines.append(
                f"game build {install.build_id}: extracted {len(extracted.assets)} assets, "
                f"{len(extracted.main_quests)} main quests"
            )
            if not extracted.main_quests:
                raise RuntimeError("extraction found no story data")

        from PySide6.QtWidgets import QApplication

        from dressmaker_editor.app_state import AppState
        from dressmaker_editor.gui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            state = AppState(Path(tmp), discover=False)
            window = MainWindow(state, load_async=False)
            window.close()
        app.processEvents()
        lines.append("window: ok")
    except Exception:
        code = 1
        lines.append(traceback.format_exc())
    lines.append("PASS" if code == 0 else "FAIL")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        return self_test(Path(sys.argv[2]))
    from dressmaker_editor.gui.app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
