"""Load the catalog: from cache, from the installed game, or the bundled copy."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .catalog import SCHEMA, Catalog
from .paths import GameInstall

log = logging.getLogger(__name__)


@dataclass
class LoadedCatalog:
    catalog: Catalog
    origin: str  # "cache", "game" or "bundled"
    problem: str | None = None  # why the game's own data couldn't be used


def cache_file(cache_dir: Path, install: GameInstall) -> Path:
    if install.build_id:
        key = f"build{install.build_id}"
    else:
        resources = install.data_dir / "resources.assets"
        try:
            key = f"mtime{int(resources.stat().st_mtime)}"
        except OSError:
            key = "unknown"
    return cache_dir / f"catalog-{key}-schema{SCHEMA}.json"


def load_catalog(
    install: GameInstall | None,
    cache_dir: Path,
    progress: Callable[[str], None] | None = None,
) -> LoadedCatalog:
    if install is None:
        return LoadedCatalog(Catalog.bundled(), "bundled", "Dressmaker isn't installed here")

    path = cache_file(cache_dir, install)
    if path.is_file():
        try:
            return LoadedCatalog(Catalog.load(path), "cache")
        except (OSError, ValueError, KeyError) as exc:
            log.warning("ignoring broken catalog cache %s: %s", path, exc)

    try:
        from .extract import extract_catalog

        catalog = extract_catalog(install, progress)
    except Exception as exc:
        log.exception("could not read the game's data")
        return LoadedCatalog(
            Catalog.bundled(), "bundled", f"couldn't read the installed game's data ({exc})"
        )
    if not catalog.main_quests:
        return LoadedCatalog(
            Catalog.bundled(), "bundled", "the installed game's story data wasn't found"
        )
    try:
        catalog.save(path)
    except OSError as exc:
        log.warning("could not cache the catalog: %s", exc)
    return LoadedCatalog(catalog, "game")
