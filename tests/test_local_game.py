"""Checks against the real game and saves on this machine.

Skipped in CI and on machines without Dressmaker. They prove the writer
reproduces whatever the installed game wrote, byte for byte.
"""

from __future__ import annotations

import pytest

from dressmaker_editor import gamejson
from dressmaker_editor.extract import extract_catalog
from dressmaker_editor.paths import find_game_install, find_save_dir
from dressmaker_editor.slots import SaveFolder
from dressmaker_editor.validate import errors, validate

pytestmark = pytest.mark.game

INSTALL = find_game_install()
SAVES = find_save_dir()


def local_save_files():
    if SAVES is None:
        return []
    return sorted(
        p
        for p in SAVES.iterdir()
        if p.is_file() and p.name.startswith("player") and ".json" in p.name
    )


@pytest.fixture(scope="module")
def game_catalog():
    if INSTALL is None:
        pytest.skip("Dressmaker isn't installed")
    return extract_catalog(INSTALL)


@pytest.mark.skipif(SAVES is None, reason="no Dressmaker saves on this machine")
@pytest.mark.parametrize("path", local_save_files(), ids=lambda p: p.name)
def test_real_saves_round_trip(path):
    text = path.read_bytes().decode("utf-8")
    assert gamejson.dumps(gamejson.loads(text)) == text


@pytest.mark.skipif(SAVES is None, reason="no Dressmaker saves on this machine")
def test_real_saves_validate_and_caches_match(game_catalog):
    folder = SaveFolder(SAVES)
    for slot in range(3):
        if not folder.exists(slot):
            continue
        save = folder.read(slot)
        assert errors(validate(save.tree, game_catalog)) == []
        if folder.cache_path(slot).is_file():
            expected = folder.cache_path(slot).read_bytes().decode("utf-8")
            assert gamejson.dumps(folder.build_cache(save)) == expected


def test_bundled_snapshot_matches_installed_game(game_catalog, catalog):
    if game_catalog.source.get("build_id") != catalog.source.get("build_id"):
        pytest.skip("the installed game is a different build; refresh the snapshot")
    assert game_catalog.main_quests == catalog.main_quests
    assert game_catalog.side_quests == catalog.side_quests
    assert set(game_catalog.assets) == set(catalog.assets)
