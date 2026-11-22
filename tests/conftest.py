from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dressmaker_editor.catalog import Catalog
from dressmaker_editor.savefile import SaveData
from dressmaker_editor.slots import BackupStore, SaveFolder

FIXTURES = Path(__file__).parent / "fixtures"
# A valid 1x1 PNG, standing in for dress photos.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000000000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return Catalog.bundled()


@pytest.fixture
def fresh() -> SaveData:
    return SaveData.load(FIXTURES / "fresh.json")


@pytest.fixture
def midgame() -> SaveData:
    return SaveData.load(FIXTURES / "midgame.json")


@pytest.fixture
def folder(tmp_path: Path) -> SaveFolder:
    """A save folder with the fresh game in slot 1 and the mid-game save in slot 2."""
    root = tmp_path / "saves"
    root.mkdir()
    shutil.copy(FIXTURES / "fresh.json", root / "player.json")
    shutil.copy(FIXTURES / "fresh.json.cache", root / "player.json.cache")
    shutil.copy(FIXTURES / "midgame.json", root / "player1.json")
    return SaveFolder(root)


@pytest.fixture
def backups(tmp_path: Path) -> BackupStore:
    return BackupStore(tmp_path / "backups")
