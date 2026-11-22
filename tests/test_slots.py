from __future__ import annotations

import json
import zipfile

import pytest

from conftest import FIXTURES, PNG
from dressmaker_editor import gamejson
from dressmaker_editor.slots import (
    GameRunningError,
    InvalidSaveError,
    SaveError,
    slot_file_name,
)


def test_slot_file_names():
    assert [slot_file_name(i) for i in range(3)] == ["player.json", "player1.json", "player2.json"]


def test_slots_overview(folder):
    slots = folder.slots()
    assert [s.exists for s in slots] == [True, True, False]
    assert slots[0].player_name == "Dressmaker"
    assert slots[1].prestige == pytest.approx(57.0171, abs=1e-3)


def test_cache_matches_what_the_game_writes(folder):
    cache = gamejson.dumps(folder.build_cache(folder.read(0)))
    assert cache == (FIXTURES / "fresh.json.cache").read_bytes().decode("utf-8")


def test_write_backs_up_and_refreshes_cache(folder, backups, catalog):
    save = folder.read(1)
    save.gold = 123
    save.player_name = "Atelier Rose"
    folder.write(1, save, catalog=catalog, backups=backups, reason="edit")
    assert folder.read(1).gold == 123
    cache = gamejson.loads(folder.cache_path(1).read_text(encoding="utf-8"))
    assert cache["playerName"] == "Atelier Rose"
    [backup] = backups.list()
    assert backup.slot == 1
    assert backup.save_path.read_bytes() == (FIXTURES / "midgame.json").read_bytes()


def test_write_keeps_untouched_files_byte_exact(folder, backups, catalog):
    folder.write(0, folder.read(0), catalog=catalog, backups=backups, reason="noop")
    assert folder.slot_path(0).read_bytes() == (FIXTURES / "fresh.json").read_bytes()
    assert folder.cache_path(0).read_bytes() == (FIXTURES / "fresh.json.cache").read_bytes()


def test_invalid_saves_are_refused_and_nothing_changes(folder, backups, catalog):
    before = folder.slot_path(0).read_bytes()
    save = folder.read(0)
    save.tree["unlockedFabrics"].append("0123456789abcdef0123456789abcdef,Imaginary Silk")
    with pytest.raises(InvalidSaveError):
        folder.write(0, save, catalog=catalog, backups=backups, reason="edit")
    assert folder.slot_path(0).read_bytes() == before
    assert backups.list() == []


def test_refuses_while_game_runs(folder, backups, catalog):
    with pytest.raises(GameRunningError):
        folder.write(
            0,
            folder.read(0),
            catalog=catalog,
            backups=backups,
            reason="x",
            is_game_running=lambda: True,
        )


def _with_photo(folder, slot: int, guid: str) -> None:
    save = folder.read(slot)
    save.completed_quests[0]["thumbnailGuid"] = guid
    folder.thumbnails_dir.mkdir(exist_ok=True)
    folder.thumbnail_path(guid).write_bytes(PNG)
    folder.write(slot, save, catalog=None, backups=None, reason="setup")


def test_copy_slot_gives_photos_new_names(folder, backups, catalog):
    _with_photo(folder, 1, "a" * 32)
    folder.copy_slot(1, 2, catalog=catalog, backups=backups)
    copied = folder.read(2)
    new_guid = copied.completed_quests[0]["thumbnailGuid"]
    assert new_guid != "a" * 32
    assert folder.thumbnail_path(new_guid).read_bytes() == PNG
    assert folder.thumbnail_path("a" * 32).is_file()
    assert copied.gold == folder.read(1).gold
    assert folder.slot_info(2).thumbnail == folder.thumbnail_path(new_guid)


def test_copy_to_same_slot_is_refused(folder, backups, catalog):
    with pytest.raises(SaveError):
        folder.copy_slot(1, 1, catalog=catalog, backups=backups)


def test_export_zip_and_import_round_trip(folder, backups, catalog, tmp_path):
    _with_photo(folder, 1, "b" * 32)
    archive = folder.export(1, tmp_path / "out.zip", game_build="123")
    with zipfile.ZipFile(archive) as zf:
        assert json.loads(zf.read("dressmaker-save.json"))["slot"] == 2
        assert f"thumbnails/{'b' * 32}.png" in zf.namelist()
        assert zf.read("player.json") == folder.slot_path(1).read_bytes()
    folder.import_file(archive, 2, catalog=catalog, backups=backups)
    imported = folder.read(2)
    assert imported.gold == folder.read(1).gold
    guid = imported.completed_quests[0]["thumbnailGuid"]
    assert guid != "b" * 32 and folder.thumbnail_path(guid).read_bytes() == PNG


def test_export_and_import_plain_json(folder, backups, catalog, tmp_path):
    exported = folder.export(0, tmp_path / "slot1.json")
    assert exported.read_bytes() == folder.slot_path(0).read_bytes()
    folder.import_file(exported, 2, catalog=catalog, backups=backups)
    assert folder.slot_path(2).read_bytes() == folder.slot_path(0).read_bytes()


def test_import_overwrite_is_backed_up(folder, backups, catalog, tmp_path):
    folder.import_file(FIXTURES / "fresh.json", 1, catalog=catalog, backups=backups)
    [backup] = backups.list()
    assert backup.reason == "import"
    assert backup.save_path.read_bytes() == (FIXTURES / "midgame.json").read_bytes()


def test_import_rejects_garbage(folder, backups, catalog, tmp_path):
    junk = tmp_path / "junk.json"
    junk.write_text("not a save", encoding="utf-8")
    with pytest.raises(SaveError):
        folder.import_file(junk, 2, catalog=catalog, backups=backups)
    assert not folder.exists(2)


def test_backups_are_pruned(folder, tmp_path):
    from dressmaker_editor.slots import BackupStore

    store = BackupStore(tmp_path / "few", keep=2)
    for _ in range(4):
        store.backup(folder, 0, "edit")
    assert len(store.list()) == 2
