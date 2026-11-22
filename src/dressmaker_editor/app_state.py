"""Everything the window needs that isn't a widget: folders, game data, settings."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .gamedata import LoadedCatalog, load_catalog
from .paths import GameInstall, app_data_dir, find_game_install, find_save_dir, game_install_at
from .slots import BackupStore, SaveFolder

log = logging.getLogger(__name__)


@dataclass
class Settings:
    path: Path
    save_dir: str | None = None
    game_dir: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Settings:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return cls(
            path=path,
            save_dir=data.pop("save_dir", None),
            game_dir=data.pop("game_dir", None),
            extra=data,
        )

    def save(self) -> None:
        data = {**self.extra, "save_dir": self.save_dir, "game_dir": self.game_dir}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(json.dumps(data, indent=2).encode("utf-8"))
        except OSError as exc:
            log.warning("could not save settings: %s", exc)


class AppState:
    def __init__(self, data_dir: Path | None = None, *, discover: bool = True):
        self.data_dir = data_dir or app_data_dir()
        self.settings = Settings.load(self.data_dir / "settings.json")
        self.backups = BackupStore(self.data_dir / "backups")
        self.install: GameInstall | None = None
        self.folder: SaveFolder | None = None
        self.loaded: LoadedCatalog | None = None
        if discover:
            self.locate()

    def locate(self) -> None:
        if self.settings.game_dir:
            self.install = game_install_at(Path(self.settings.game_dir))
        if self.install is None:
            self.install = find_game_install()
        save_dir = Path(self.settings.save_dir) if self.settings.save_dir else find_save_dir()
        self.folder = SaveFolder(save_dir) if save_dir else None

    def set_save_dir(self, path: Path) -> None:
        self.settings.save_dir = str(path)
        self.settings.save()
        self.folder = SaveFolder(path)

    def set_game_dir(self, path: Path) -> GameInstall | None:
        install = game_install_at(path)
        if install is not None:
            self.settings.game_dir = str(path)
            self.settings.save()
            self.install = install
        return install

    def load_catalog(self, progress: Callable[[str], None] | None = None) -> LoadedCatalog:
        self.loaded = load_catalog(self.install, self.data_dir / "cache", progress)
        return self.loaded

    @property
    def catalog(self):
        return self.loaded.catalog if self.loaded else None
