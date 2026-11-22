"""Build the single-file Windows executable with PyInstaller.

    uv sync --group build
    uv run python scripts/build_exe.py

The result is dist/DressmakerSaveEditor.exe. Check it with
``dist/DressmakerSaveEditor.exe --self-test report.txt``.

Builds are development builds (version ``X.Y.Z+dev.<commit>``) unless
``DRESSMAKER_RELEASE=true`` is set, which the release workflow does.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
NAME = "DressmakerSaveEditor"
BUILD = ROOT / "build"

sys.path.insert(0, str(ROOT / "src"))
from dressmaker_editor import __version__  # noqa: E402

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('FileDescription', 'Dressmaker Save Editor'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', '{name}'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', '{name}.exe'),
        StringStruct('ProductName', 'Dressmaker Save Editor'),
        StringStruct('ProductVersion', '{version}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def build_metadata() -> str:
    """Semver build metadata: empty for releases, ``dev.<commit>`` otherwise."""
    if os.environ.get("DRESSMAKER_RELEASE") == "true":
        return ""
    commit = os.environ.get("GITHUB_SHA", "")[:7]
    return f"dev.{commit}" if commit else "dev"


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    metadata = build_metadata()
    version = f"{__version__}+{metadata}" if metadata else __version__
    numbers = (*(int(n) for n in re.findall(r"\d+", __version__)[:3]), 0)

    version_file = BUILD / "version_info.txt"
    version_file.write_text(
        VERSION_INFO.format(numbers=numbers, version=version, name=NAME), encoding="utf-8"
    )
    build_info = BUILD / "build.txt"
    build_info.write_text(metadata, encoding="utf-8")
    print(f"building {NAME} {version}")

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
            "--version-file",
            str(version_file),
            "--paths",
            str(ROOT / "src"),
            # The bundled catalog, placeholder dress and icon.
            "--collect-data",
            "dressmaker_editor",
            # Build metadata for full_version(); empty for releases.
            "--add-data",
            f"{build_info}{os.pathsep}dressmaker_editor/data",
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
            str(BUILD / "pyinstaller"),
            "--specpath",
            str(BUILD),
        ]
    )


if __name__ == "__main__":
    main()
