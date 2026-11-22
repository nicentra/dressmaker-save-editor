"""Find Steam, the Dressmaker install, its save folder and our own data folder."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

STEAM_APP_ID = "4019220"
COMPANY = "Cozy Lives"
PRODUCT = "Dressmaker"
APP_DIR_NAME = "DressmakerSaveEditor"


@dataclass(frozen=True)
class GameInstall:
    root: Path
    build_id: str | None

    @property
    def data_dir(self) -> Path:
        return self.root / f"{PRODUCT}_Data"

    @property
    def managed_dir(self) -> Path:
        return self.data_dir / "Managed"


def parse_vdf(text: str) -> dict:
    """Parse Valve's KeyValues text format (``libraryfolders.vdf``, ``*.acf``)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    stack: list[dict] = [{}]
    key: str | None = None
    for quoted, brace in tokens:
        if brace == "{":
            child: dict = {}
            stack[-1][key or ""] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = quoted.replace("\\\\", "\\")
        else:
            stack[-1][key] = quoted.replace("\\\\", "\\")
            key = None
    return stack[0]


def steam_roots() -> list[Path]:
    """Steam installation folders on this machine, most likely first."""
    found: list[Path] = []
    if sys.platform == "win32":
        found += _steam_roots_from_registry()
        found.append(Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam")
    else:
        home = Path.home()
        found += [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
            home / "Library" / "Application Support" / "Steam",
        ]
    unique: list[Path] = []
    for root in found:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved.is_dir() and resolved not in unique:
            unique.append(resolved)
    return unique


def _steam_roots_from_registry() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []
    roots = []
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
        except OSError:
            continue
    return roots


def steam_libraries() -> list[Path]:
    """Every Steam library folder (the folders that contain ``steamapps``)."""
    libraries: list[Path] = []
    for root in steam_roots():
        candidates = [root]
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            data = parse_vdf(vdf.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            data = {}
        for entry in data.get("libraryfolders", {}).values():
            if isinstance(entry, dict) and entry.get("path"):
                candidates.append(Path(entry["path"]))
        for lib in candidates:
            if (lib / "steamapps").is_dir() and lib not in libraries:
                libraries.append(lib)
    return libraries


def find_game_install() -> GameInstall | None:
    """Locate Dressmaker through Steam's app manifests."""
    for lib in steam_libraries():
        manifest = lib / "steamapps" / f"appmanifest_{STEAM_APP_ID}.acf"
        if not manifest.is_file():
            continue
        try:
            state = parse_vdf(manifest.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        app = state.get("AppState", {})
        root = lib / "steamapps" / "common" / app.get("installdir", PRODUCT)
        install = GameInstall(root=root, build_id=app.get("buildid"))
        if install.data_dir.is_dir():
            return install
    return None


def game_install_at(root: Path) -> GameInstall | None:
    """Treat a folder the user picked as the game install, if it looks like one."""
    install = GameInstall(root=root, build_id=None)
    if not install.data_dir.is_dir():
        return None
    manifest = root.parent.parent / f"appmanifest_{STEAM_APP_ID}.acf"
    if manifest.is_file():
        try:
            state = parse_vdf(manifest.read_text(encoding="utf-8", errors="replace"))
            install = GameInstall(root=root, build_id=state.get("AppState", {}).get("buildid"))
        except OSError:
            pass
    return install


def local_low() -> Path:
    """Windows' ``AppData\\LocalLow`` folder, where Unity games keep their saves."""
    if sys.platform == "win32":
        path = _known_folder_local_low()
        if path:
            return path
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "LocalLow"


def _known_folder_local_low() -> Path | None:
    try:
        import ctypes
        from ctypes import wintypes
        from uuid import UUID
    except ImportError:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    uid = UUID("{A520A1A4-1780-4FF6-BD18-167343C5AF16}")  # FOLDERID_LocalAppDataLow
    guid = GUID.from_buffer_copy(uid.bytes_le)
    out = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
    except AttributeError:
        return None
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value) if out.value else None
    finally:
        ole32.CoTaskMemFree(out)


def find_save_dir() -> Path | None:
    """The folder holding ``player.json`` and friends, if it exists."""
    for candidate in save_dir_candidates():
        if candidate.is_dir():
            return candidate
    return None


def save_dir_candidates() -> list[Path]:
    candidates = []
    if sys.platform == "win32":
        candidates.append(local_low() / COMPANY / PRODUCT)
    # Proton (Linux and Steam Deck) keeps a Windows profile per game.
    for lib in steam_libraries():
        candidates.append(
            lib
            / "steamapps"
            / "compatdata"
            / STEAM_APP_ID
            / "pfx"
            / "drive_c"
            / "users"
            / "steamuser"
            / "AppData"
            / "LocalLow"
            / COMPANY
            / PRODUCT
        )
    if sys.platform != "win32":
        candidates.append(Path.home() / ".config" / "unity3d" / COMPANY / PRODUCT)
    return candidates


def app_data_dir() -> Path:
    """Where the editor keeps its settings, backups and cached game data."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_DIR_NAME
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "dressmaker-save-editor"
