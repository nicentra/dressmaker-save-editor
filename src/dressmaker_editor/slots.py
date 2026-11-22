"""Save slots on disk: reading, safe writing, copying, import/export, backups.

Dressmaker has three slots. Slot 1 in the game's menu is ``player.json``,
slots 2 and 3 are ``player1.json`` and ``player2.json``. Next to each the game
keeps a ``.cache`` summary for the slot menu and a ``.lastgood`` copy it falls
back to when a save fails to load. Dress photos live in ``thumbnails/`` and
are shared by all slots, which is why copies get photos under new names: when
the game deletes a slot it deletes that slot's photos.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import full_version, gamejson
from .catalog import Catalog
from .savefile import SaveData
from .validate import Issue, errors, validate

SLOT_COUNT = 3
EXPORT_FORMAT = "dressmaker-save-editor/1"
EXPORT_MANIFEST = "dressmaker-save.json"


def slot_file_name(slot: int) -> str:
    """``PlayerProgress.SlotFileName``: slot 0 is ``player.json``."""
    return "player.json" if slot == 0 else f"player{slot}.json"


def slot_label(slot: int) -> str:
    """How the game's menu numbers the slot."""
    return f"Slot {slot + 1}"


class SaveError(Exception):
    """A save couldn't be written; nothing on disk was changed."""


class InvalidSaveError(SaveError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        lines = "\n".join(f"• {issue}" for issue in errors(issues)[:10])
        super().__init__(f"The save has problems the game can't load:\n{lines}")


class GameRunningError(SaveError):
    def __init__(self) -> None:
        super().__init__(
            "Dressmaker is running. It autosaves every minute and would overwrite your "
            "changes, so close the game first."
        )


@dataclass
class SlotInfo:
    slot: int
    path: Path
    exists: bool
    modified: datetime | None
    player_name: str | None = None
    prestige: float | None = None
    thumbnail: Path | None = None

    @property
    def label(self) -> str:
        return slot_label(self.slot)


def atomic_write(path: Path, data: bytes) -> None:
    """Write a file so that a crash never leaves it half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".editor-tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class SaveFolder:
    def __init__(self, path: Path):
        self.path = Path(path)

    def __repr__(self) -> str:
        return f"SaveFolder({str(self.path)!r})"

    # ── paths ─────────────────────────────────────────────────────────────
    def slot_path(self, slot: int) -> Path:
        return self.path / slot_file_name(slot)

    def cache_path(self, slot: int) -> Path:
        return self.path / (slot_file_name(slot) + ".cache")

    @property
    def thumbnails_dir(self) -> Path:
        return self.path / "thumbnails"

    def thumbnail_path(self, guid: str) -> Path:
        return self.thumbnails_dir / f"{guid}.png"

    def thumbnail_exists(self, guid: str | None) -> bool:
        return bool(guid) and self.thumbnail_path(guid).is_file()

    # ── reading ───────────────────────────────────────────────────────────
    def slots(self) -> list[SlotInfo]:
        return [self.slot_info(slot) for slot in range(SLOT_COUNT)]

    def slot_info(self, slot: int) -> SlotInfo:
        path = self.slot_path(slot)
        if not path.is_file():
            return SlotInfo(slot, path, False, None)
        info = SlotInfo(slot, path, True, datetime.fromtimestamp(path.stat().st_mtime))
        cache = self._read_cache(slot)
        if cache is None:
            try:
                cache = self.build_cache(self.read(slot))
            except OSError, ValueError:
                cache = {}
        info.player_name = cache.get("playerName")
        prestige = cache.get("prestige")
        info.prestige = float(prestige) if isinstance(prestige, (int, float)) else None
        guid = cache.get("lastDressThumbnailGuid")
        if isinstance(guid, str) and self.thumbnail_exists(guid):
            info.thumbnail = self.thumbnail_path(guid)
        return info

    def _read_cache(self, slot: int) -> dict[str, Any] | None:
        try:
            cache = gamejson.loads(self.cache_path(slot).read_bytes().decode("utf-8"))
        except OSError, ValueError, UnicodeDecodeError:
            return None
        return cache if isinstance(cache, dict) else None

    def exists(self, slot: int) -> bool:
        return self.slot_path(slot).is_file()

    def read(self, slot: int) -> SaveData:
        return SaveData.load(self.slot_path(slot))

    # ── the slot menu's summary ───────────────────────────────────────────
    def build_cache(self, save: SaveData) -> dict[str, Any]:
        """``PlayerProgress.BuildCache``: what the game's slot menu shows."""
        prestige = save.tree.get("prestige", 0.0)
        return {
            "playerName": save.tree.get("playerName"),
            "prestige": prestige if isinstance(prestige, float) else float(prestige or 0),
            "lastDressThumbnailGuid": self._last_dress_thumbnail(save),
            "thumbnailGuids": save.thumbnail_guids(),
        }

    def _last_dress_thumbnail(self, save: SaveData) -> str | None:
        current = save.current_quest_state()
        if current and self.thumbnail_exists(current.get("thumbnailGuid")):
            return current["thumbnailGuid"]
        for state in reversed(save.completed_quests):
            if isinstance(state, dict) and self.thumbnail_exists(state.get("thumbnailGuid")):
                return state["thumbnailGuid"]
        for state in reversed(save.active_commissions):
            if isinstance(state, dict) and self.thumbnail_exists(state.get("thumbnailGuid")):
                return state["thumbnailGuid"]
        return None

    # ── writing ───────────────────────────────────────────────────────────
    def known_guids(self) -> frozenset[str]:
        """Asset guids referenced by the saves already in this folder.

        The game wrote those, so the installed version has them even when the
        editor's bundled catalog (possibly older than the game) doesn't.
        """
        found: set[str] = set()
        for path in sorted(self.path.glob("player*.json*")):
            if path.name.endswith((".cache", ".tmp", ".editor-tmp")):
                continue
            try:
                found |= SaveData.load(path).loaded_guids
            except OSError, ValueError, UnicodeDecodeError:
                continue
        return frozenset(found)

    def check(
        self,
        save: SaveData,
        *,
        catalog: Catalog | None,
        is_game_running: Callable[[], bool] | None = None,
    ) -> tuple[str, SaveData]:
        """Everything :meth:`write` checks before touching the disk.

        Returns the text to write and what the game will read back from it.
        """
        if is_game_running is not None and is_game_running():
            raise GameRunningError()
        if catalog is None:
            raise SaveError("The game's data hasn't loaded yet, so the save can't be checked.")
        text = save.dumps()
        try:
            reparsed = SaveData.loads(text)  # what the game will read back
        except ValueError as exc:
            raise SaveError(f"The save couldn't be read back after writing it: {exc}") from exc
        trusted = self.known_guids() if catalog.is_bundled else frozenset()
        issues = validate(reparsed.tree, catalog, trusted)
        if errors(issues):
            raise InvalidSaveError(issues)
        return text, reparsed

    def write(
        self,
        slot: int,
        save: SaveData,
        *,
        catalog: Catalog | None,
        backups: BackupStore | None,
        reason: str,
        is_game_running: Callable[[], bool] | None = None,
    ) -> Path:
        """Validate, back up the old slot, then replace it atomically."""
        text, reparsed = self.check(save, catalog=catalog, is_game_running=is_game_running)
        if backups is not None:
            backups.backup(self, slot, reason)
        atomic_write(self.slot_path(slot), text.encode("utf-8"))
        cache = gamejson.dumps(self.build_cache(reparsed))
        atomic_write(self.cache_path(slot), cache.encode("utf-8"))
        return self.slot_path(slot)

    def _write_with_photos(
        self,
        slot: int,
        save: SaveData,
        read_thumbnail: Callable[[str], bytes | None],
        *,
        catalog: Catalog | None,
        backups: BackupStore | None,
        reason: str,
        is_game_running: Callable[[], bool] | None = None,
    ) -> Path:
        """Copy a save's dress photos under new names, then write it.

        The save is checked first, and the new photos are removed again if the
        write fails, so a refused copy or import leaves nothing behind.
        """
        self.check(save, catalog=catalog, is_game_running=is_game_running)
        save, created = self.copy_thumbnails(save, read_thumbnail)
        try:
            return self.write(
                slot,
                save,
                catalog=catalog,
                backups=backups,
                reason=reason,
                is_game_running=is_game_running,
            )
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    def copy_thumbnails(
        self, save: SaveData, read_thumbnail: Callable[[str], bytes | None]
    ) -> tuple[SaveData, list[Path]]:
        """Give every dress photo in ``save`` a fresh name in this folder.

        Returns the updated copy of the save and the photo files created.
        Photos the source doesn't have keep their old name; the game shows a
        placeholder for missing photos.
        """
        save = save.copy()
        created: list[Path] = []
        for state in save.quest_states():
            guid = state.get("thumbnailGuid")
            if not isinstance(guid, str) or not guid:
                continue
            data = read_thumbnail(guid)
            if data is None:
                continue
            new_guid = uuid.uuid4().hex
            path = self.thumbnail_path(new_guid)
            atomic_write(path, data)
            created.append(path)
            state["thumbnailGuid"] = new_guid
        return save, created

    def read_thumbnail(self, guid: str) -> bytes | None:
        try:
            return self.thumbnail_path(guid).read_bytes()
        except OSError:
            return None

    def copy_slot(self, source: int, target: int, **write_args: Any) -> Path:
        if source == target:
            raise SaveError("Pick a different slot to copy to.")
        return self._write_with_photos(
            target,
            self.read(source),
            self.read_thumbnail,
            reason=f"copy-from-slot{source + 1}",
            **write_args,
        )

    # ── import / export ───────────────────────────────────────────────────
    def export(self, slot: int, destination: Path, *, game_build: str | None = None) -> Path:
        """Write a slot to ``.zip`` (save plus dress photos) or plain ``.json``."""
        data = self.slot_path(slot).read_bytes()
        if destination.suffix.lower() == ".json":
            atomic_write(destination, data)
            return destination
        save = SaveData.loads(data.decode("utf-8"))
        manifest = {
            "format": EXPORT_FORMAT,
            "editor_version": full_version(),
            "game_build": game_build,
            "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "slot": slot + 1,
            "player_name": save.player_name,
            "prestige": save.prestige,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(EXPORT_MANIFEST, json.dumps(manifest, indent=2))
            zf.writestr("player.json", data)
            for guid in dict.fromkeys(save.thumbnail_guids()):
                photo = self.read_thumbnail(guid)
                if photo is not None:
                    zf.writestr(f"thumbnails/{guid}.png", photo)
        atomic_write(destination, buffer.getvalue())
        return destination

    def import_file(
        self, source: Path, slot: int, *, reason: str = "import", **write_args: Any
    ) -> Path:
        save, read_thumbnail = read_import(source)
        if read_thumbnail is None:
            # A bare .json: its photos (if any) are this folder's own.
            read_thumbnail = self.read_thumbnail
        return self._write_with_photos(slot, save, read_thumbnail, reason=reason, **write_args)


def read_import(source: Path) -> tuple[SaveData, Callable[[str], bytes | None] | None]:
    """Load a save from a ``.json`` or an exported ``.zip``.

    Returns the save and, for zips, a way to read the dress photos inside.
    """
    data = source.read_bytes()
    if not zipfile.is_zipfile(io.BytesIO(data)):
        try:
            return SaveData.loads(data.decode("utf-8")), None
        except (UnicodeDecodeError, ValueError) as exc:
            raise SaveError(f"{source.name} isn't a Dressmaker save: {exc}") from exc

    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    candidates = [n for n in names if n.rsplit("/", 1)[-1] == "player.json"] or [
        n for n in names if n.rsplit("/", 1)[-1].startswith("player") and n.endswith(".json")
    ]
    if not candidates:
        raise SaveError(f"{source.name} doesn't contain a player.json")
    entry = min(candidates, key=len)
    try:
        save = SaveData.loads(zf.read(entry).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SaveError(f"{entry} in {source.name} isn't a Dressmaker save: {exc}") from exc
    prefix = entry[: -len(entry.rsplit("/", 1)[-1])]

    def read_thumbnail(guid: str) -> bytes | None:
        for name in (f"{prefix}thumbnails/{guid}.png", f"thumbnails/{guid}.png"):
            if name in names:
                return zf.read(name)
        return None

    return save, read_thumbnail


# ── backups ───────────────────────────────────────────────────────────────
@dataclass
class Backup:
    path: Path
    slot: int
    reason: str
    created: datetime
    player_name: str | None
    prestige: float | None

    @property
    def save_path(self) -> Path:
        return self.path / "player.json"


class BackupStore:
    """Copies of slots taken before the editor overwrites them.

    They live in the editor's own data folder, not the save folder, so Steam
    Cloud doesn't sync them.
    """

    def __init__(self, root: Path, keep: int = 200):
        self.root = Path(root)
        self.keep = keep

    def backup(self, folder: SaveFolder, slot: int, reason: str) -> Path | None:
        source = folder.slot_path(slot)
        if not source.is_file():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.root / f"{stamp}-slot{slot + 1}-{_slug(reason)}"
        suffix = 1
        while target.exists():
            suffix += 1
            target = self.root / f"{stamp}-slot{slot + 1}-{_slug(reason)}-{suffix}"
        target.mkdir(parents=True)
        shutil.copy2(source, target / "player.json")
        cache = folder.cache_path(slot)
        if cache.is_file():
            shutil.copy2(cache, target / "player.json.cache")
        info: dict[str, Any] = {
            "slot": slot + 1,
            "reason": reason,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": str(source),
        }
        try:
            save = SaveData.load(source)
            info["player_name"] = save.player_name
            info["prestige"] = save.prestige
        except OSError, ValueError:
            pass
        (target / "backup.json").write_bytes(json.dumps(info, indent=2).encode("utf-8"))
        self.prune()
        return target

    def list(self) -> list[Backup]:
        if not self.root.is_dir():
            return []
        out = []
        for entry in self.root.iterdir():
            meta = entry / "backup.json"
            if not (entry / "player.json").is_file() or not meta.is_file():
                continue
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(info["created"])
            except OSError, ValueError, KeyError:
                continue
            out.append(
                Backup(
                    path=entry,
                    slot=int(info.get("slot", 1)) - 1,
                    reason=str(info.get("reason", "")),
                    created=created,
                    player_name=info.get("player_name"),
                    prestige=info.get("prestige"),
                )
            )
        out.sort(key=lambda b: b.created, reverse=True)
        return out

    def prune(self) -> None:
        for old in self.list()[self.keep :]:
            shutil.rmtree(old.path, ignore_errors=True)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c == "-" else "-" for c in text.lower()).strip("-") or "save"
