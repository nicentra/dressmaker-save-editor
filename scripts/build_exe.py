"""Build the single-file Windows executable with PyInstaller.

    uv sync --group build
    uv run python scripts/build_exe.py

The result is dist/DressmakerSaveEditor.exe. Check it with
``dist/DressmakerSaveEditor.exe --self-test report.txt``.
"""

from __future__ import annotations

from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
NAME = "DressmakerSaveEditor"


def main() -> None:
    PyInstaller.__main__.run(
        [
            str(ROOT / "src" / "dressmaker_editor" / "__main__.py"),
            "--name",
            NAME,
            "--onefile",
            "--windowed",
            "--noconfirm",
            "--clean",
            "--icon",
            str(ROOT / "packaging" / "icon.ico"),
            "--paths",
            str(ROOT / "src"),
            # The bundled catalog, placeholder dress and icon.
            "--collect-data",
            "dressmaker_editor",
            # UnityPy ships type databases as data files and a native speed-up.
            "--collect-all",
            "UnityPy",
            # The type tree generator is a native DLL loaded with ctypes.
            "--collect-all",
            "TypeTreeGeneratorAPI",
            "--exclude-module",
            "tkinter",
            "--distpath",
            str(ROOT / "dist"),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
            "--specpath",
            str(ROOT / "build"),
        ]
    )


if __name__ == "__main__":
    main()
