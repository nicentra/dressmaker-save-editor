"""Drive the real window offscreen: edit, jump the story, replay a quest, save."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from dressmaker_editor.app_state import AppState
from dressmaker_editor.gui import main_window, quests_tab, story_tab
from dressmaker_editor.story import Status, Story


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, folder, tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "game_is_running", lambda: False)
    monkeypatch.setattr(story_tab, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(quests_tab, "confirm", lambda *a, **k: True)
    state = AppState(tmp_path / "appdata", discover=False)
    state.folder = folder
    win = main_window.MainWindow(state, load_async=False)
    yield win
    win.doc = None  # don't ask about unsaved changes
    win.close()


def test_opens_first_slot(window):
    assert window.doc is not None and window.doc.slot == 0
    assert window.slot_list.count() == 3
    assert not window.save_action.isEnabled()


def test_edit_gold_and_save(window, folder):
    window.overview.gold.setValue(4321)
    assert window.doc.dirty and window.save_action.isEnabled()
    assert window.windowTitle().endswith("*")
    assert window.save_document()
    assert folder.read(0).gold == 4321
    assert not window.doc.dirty
    assert len(window.state.backups.list()) == 1


def test_jump_story_from_the_story_tab(window, folder, catalog):
    window.slot_list.setCurrentRow(1)
    assert window.doc.slot == 1
    window.story.table.selectRow(20)
    window.story._jump_to_selected()
    assert window.doc.dirty
    assert window.save_document()
    assert Story(folder.read(1), catalog).position() == 20


def test_replay_side_quest_from_the_quests_tab(window, folder, catalog):
    window.slot_list.setCurrentRow(1)
    tree = window.quests.tree
    done = None
    for group in range(tree.topLevelItemCount()):
        parent = tree.topLevelItem(group)
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item.data(3, quests_tab.GUID_ROLE) is Status.COMPLETED:
                done = item
    assert done is not None
    guid = done.data(0, quests_tab.GUID_ROLE)
    done.setSelected(True)
    window.quests._replay()
    assert window.save_document()
    assert not Story(folder.read(1), catalog).is_completed(guid)


def test_switching_slots_with_unsaved_changes_asks(window, monkeypatch):
    window.overview.gold.setValue(1)
    monkeypatch.setattr(
        main_window.QMessageBox,
        "question",
        lambda *a, **k: main_window.QMessageBox.StandardButton.Cancel,
    )
    window.slot_list.setCurrentRow(1)
    assert window.doc.slot == 0 and window.doc.dirty
    assert window.slot_list.currentRow() == 0


def test_saving_is_blocked_while_the_game_runs(window, monkeypatch):
    window.overview.gold.setValue(2)
    monkeypatch.setattr(main_window, "game_is_running", lambda: True)
    window._check_game()
    assert not window.save_action.isEnabled()
