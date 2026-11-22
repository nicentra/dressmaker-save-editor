"""The editor's main window."""

from __future__ import annotations

import logging
import traceback
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..app_state import AppState
from ..gamedata import LoadedCatalog
from ..process import game_is_running
from ..savefile import SaveData
from ..slots import SLOT_COUNT, InvalidSaveError, SaveError, read_import, slot_label
from ..validate import errors, validate
from .dialogs import BackupsDialog, SlotPickerDialog, show_issues
from .overview_tab import OverviewTab
from .quests_tab import QuestsTab
from .story_tab import StoryTab
from .widgets import BannerStack, app_icon, confirm, placeholder_pixmap

log = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/nicentra/dressmaker-save-editor"
THUMB = 64


class Document:
    """The slot being edited and whether it has unsaved changes."""

    def __init__(self, slot: int, save: SaveData):
        self.slot = slot
        self.save = save
        self.dirty = False


class CatalogLoader(QObject):
    progress = Signal(str)
    done = Signal(object)

    def __init__(self, state: AppState):
        super().__init__()
        self.state = state

    def run(self) -> None:
        try:
            loaded = self.state.load_catalog(self.progress.emit)
        except Exception as exc:  # never leave the window waiting forever
            log.exception("loading game data failed")
            loaded = exc
        self.done.emit(loaded)


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, *, load_async: bool = True):
        super().__init__()
        self.state = state
        self.doc: Document | None = None
        self._game_running = False
        self._thread: QThread | None = None

        self.setWindowIcon(app_icon())
        self.resize(1180, 760)

        self.slot_list = QListWidget()
        self.slot_list.setIconSize(QSize(THUMB, THUMB))
        self.slot_list.setSpacing(4)
        self.slot_list.setMinimumWidth(260)
        self.slot_list.currentRowChanged.connect(self._slot_changed)

        self.banners = BannerStack()
        self.tabs = QTabWidget()
        self.overview = OverviewTab()
        self.story = StoryTab()
        self.quests = QuestsTab()
        self.tabs.addTab(self.overview, "Overview")
        self.tabs.addTab(self.story, "Story")
        self.tabs.addTab(self.quests, "Side commissions")
        for tab in (self.overview, self.story, self.quests):
            tab.edited.connect(self._edited)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: palette(placeholder-text); font-size: 14px;")

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.banners)
        right_layout.addWidget(self.tabs, 1)
        right_layout.addWidget(self.empty, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.slot_list)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 900])
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self._build_actions()
        self.data_label = QLabel()
        self.statusBar().addPermanentWidget(self.data_label)

        self.game_timer = QTimer(self)
        self.game_timer.setInterval(2000)
        self.game_timer.timeout.connect(self._check_game)
        self.game_timer.start()
        self._check_game()

        self._show_editor(False, "Reading the game's data…")
        self._update_title()
        self._update_actions()
        if load_async:
            self._load_catalog_async()
        else:
            self._catalog_loaded(state.load_catalog())

    # ── actions ───────────────────────────────────────────────────────────
    def _action(self, text, slot, *, icon=None, shortcut=None, tip=None) -> QAction:
        action = QAction(text, self)
        if icon is not None:
            action.setIcon(self.style().standardIcon(icon))
        if shortcut:
            action.setShortcut(shortcut)
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        sp = QStyle.StandardPixmap
        self.save_action = self._action(
            "Save",
            self.save_document,
            icon=sp.SP_DialogSaveButton,
            shortcut=QKeySequence.StandardKey.Save,
            tip="Write your changes to the slot",
        )
        self.revert_action = self._action(
            "Revert",
            self.revert,
            icon=sp.SP_BrowserReload,
            tip="Throw away unsaved changes and reload the slot",
        )
        self.import_action = self._action(
            "Import…",
            self.import_save,
            icon=sp.SP_ArrowDown,
            shortcut=QKeySequence.StandardKey.Open,
            tip="Load a save file into a slot",
        )
        self.export_action = self._action(
            "Export…",
            self.export_save,
            icon=sp.SP_ArrowUp,
            tip="Save this slot to a file you can keep or share",
        )
        self.copy_action = self._action(
            "Copy to slot…",
            self.copy_slot,
            icon=sp.SP_FileDialogNewFolder,
            tip="Copy this slot into another slot",
        )
        self.backups_action = self._action(
            "Backups…",
            self.show_backups,
            icon=sp.SP_FileDialogDetailedView,
            tip="Restore a copy the editor took before overwriting a slot",
        )
        self.open_folder_action = self._action(
            "Open save folder",
            self.open_save_folder,
            icon=sp.SP_DirOpenIcon,
            tip="Show the game's save folder in Explorer",
        )
        self.refresh_action = self._action(
            "Refresh slots", self.refresh_slots, shortcut=QKeySequence.StandardKey.Refresh
        )

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for action in (self.save_action, self.revert_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        for action in (self.import_action, self.export_action, self.copy_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.backups_action)
        toolbar.addAction(self.open_folder_action)
        self.addToolBar(toolbar)

        file_menu = self.menuBar().addMenu("&File")
        for action in (
            self.save_action,
            self.revert_action,
            None,
            self.import_action,
            self.export_action,
            self.copy_action,
            None,
            self.backups_action,
            self.open_folder_action,
            self.refresh_action,
            None,
        ):
            if action is None:
                file_menu.addSeparator()
            else:
                file_menu.addAction(action)
        file_menu.addAction(
            self._action("Quit", self.close, shortcut=QKeySequence.StandardKey.Quit)
        )

        settings = self.menuBar().addMenu("&Settings")
        settings.addAction(self._action("Choose save folder…", self.choose_save_folder))
        settings.addAction(self._action("Choose game folder…", self.choose_game_folder))
        settings.addAction(self._action("Re-read game data", self.reread_game_data))

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(
            self._action("Project page", lambda: QDesktopServices.openUrl(QUrl(PROJECT_URL)))
        )
        help_menu.addAction(self._action("About", self.about))

    def _update_actions(self) -> None:
        has_doc = self.doc is not None
        writable = not self._game_running and self.state.catalog is not None
        has_folder = self.state.folder is not None
        self.save_action.setEnabled(has_doc and self.doc.dirty and writable)
        self.revert_action.setEnabled(has_doc and self.doc.dirty)
        self.import_action.setEnabled(has_folder and writable)
        self.export_action.setEnabled(has_doc)
        self.copy_action.setEnabled(has_doc and writable)
        self.backups_action.setEnabled(has_folder)
        self.open_folder_action.setEnabled(has_folder)

    def _update_title(self) -> None:
        title = "Dressmaker Save Editor"
        if self.doc is not None:
            title += f" — {slot_label(self.doc.slot)}{' *' if self.doc.dirty else ''}"
        self.setWindowTitle(title)
        self.setWindowModified(bool(self.doc and self.doc.dirty))

    # ── game data ─────────────────────────────────────────────────────────
    def _load_catalog_async(self) -> None:
        self._thread = QThread(self)
        self._loader = CatalogLoader(self.state)
        self._loader.moveToThread(self._thread)
        self._thread.started.connect(self._loader.run)
        self._loader.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self._loader.done.connect(self._catalog_loaded)
        self._loader.done.connect(self._thread.quit)
        self._thread.start()

    def _catalog_loaded(self, loaded: LoadedCatalog | Exception) -> None:
        self.statusBar().clearMessage()
        if isinstance(loaded, Exception):
            self.banners.set(
                "data", "error", f"Couldn't load any game data: {loaded}. Please report this."
            )
            self._show_editor(False, "Game data is missing.")
            return
        source = loaded.catalog.source
        build = source.get("build_id") or "unknown"
        if loaded.origin == "bundled":
            self.banners.set(
                "data",
                "warning",
                f"<b>Using the game data that came with the editor</b> (game build {build}) "
                f"because {loaded.problem}. Items from newer game updates can't be added, "
                "but saves that already have them still work.",
            )
            self.data_label.setText(f"Game data: bundled (build {build})")
        else:
            self.banners.set("data", None)
            self.data_label.setText(f"Game data: installed game (build {build})")
        self.refresh_slots(select_first=True)

    def reread_game_data(self) -> None:
        if not self._confirm_discard():
            return
        cache_dir = self.state.data_dir / "cache"
        for path in cache_dir.glob("catalog-*.json"):
            path.unlink(missing_ok=True)
        self.doc = None
        self.state.locate()
        self._show_editor(False, "Reading the game's data…")
        self._load_catalog_async()

    # ── slots ─────────────────────────────────────────────────────────────
    def refresh_slots(self, select_first: bool = False, select: int | None = None) -> None:
        """Rebuild the slot list, then open ``select`` (or keep the open slot)."""
        folder = self.state.folder
        current = select if select is not None else (self.doc.slot if self.doc else None)
        self.slot_list.blockSignals(True)
        self.slot_list.clear()
        if folder is None:
            self.slot_list.blockSignals(False)
            self.banners.set(
                "folder",
                "error",
                "Dressmaker's save folder wasn't found. Start the game once to create it, "
                "or pick it in <b>Settings → Choose save folder…</b>",
            )
            self._show_editor(False, "No save folder.")
            self._update_actions()
            return
        self.banners.set("folder", None)
        first_existing = None
        for info in folder.slots():
            if info.exists:
                rank = "" if info.prestige is None else f" · rank {min(50, int(info.prestige))}"
                when = f"\nSaved {info.modified:%d %b %Y, %H:%M}" if info.modified else ""
                text = f"{info.label}\n{info.player_name or 'Dressmaker'}{rank}{when}"
                pixmap = QPixmap(str(info.thumbnail)) if info.thumbnail else QPixmap()
                if pixmap.isNull():
                    pixmap = placeholder_pixmap(THUMB)
                if first_existing is None:
                    first_existing = info.slot
            else:
                text = f"{info.label}\nEmpty"
                pixmap = placeholder_pixmap(THUMB, "Empty")
            scaled = pixmap.scaled(
                THUMB,
                THUMB,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon = QIcon(scaled)
            icon.addPixmap(scaled, QIcon.Mode.Selected)  # no blue tint on the photo
            item = QListWidgetItem(icon, text)
            item.setData(Qt.ItemDataRole.UserRole, info.slot)
            item.setSizeHint(QSize(240, THUMB + 16))
            self.slot_list.addItem(item)
        self.slot_list.blockSignals(False)
        target = current if current is not None else (first_existing if select_first else None)
        if target is not None:
            self._highlight(target)
            if self.doc is None or self.doc.slot != target or select is not None:
                self._open_slot(target)
        elif self.doc is None:
            self._show_editor(False, "Pick a save slot on the left.")
        self._update_actions()

    def _slot_changed(self, row: int) -> None:
        if row < 0 or row >= SLOT_COUNT:
            return
        if self.doc is not None and self.doc.slot == row:
            return
        if not self._confirm_discard():
            self._highlight(self.doc.slot)
            return
        self._open_slot(row)

    def _highlight(self, slot: int) -> None:
        """Select a slot in the list without triggering a slot switch."""
        self.slot_list.blockSignals(True)
        self.slot_list.setCurrentRow(slot)
        self.slot_list.blockSignals(False)

    def _open_slot(self, slot: int) -> None:
        folder = self.state.folder
        catalog = self.state.catalog
        self.banners.set("save", None)
        self._highlight(slot)
        if folder is None or catalog is None:
            return
        if not folder.exists(slot):
            self.doc = None
            self._show_editor(
                False,
                f"{slot_label(slot)} is empty.\n\nUse Import… to load a save file into it, or "
                "Copy to slot… from another slot.",
            )
        else:
            try:
                save = folder.read(slot)
            except (OSError, ValueError) as exc:
                self.doc = None
                self._show_editor(False, f"{slot_label(slot)} couldn't be read:\n{exc}")
                self._update_actions()
                self._update_title()
                return
            self.doc = Document(slot, save)
            trusted = save.loaded_guids if catalog.is_bundled else frozenset()
            problems = errors(validate(save.tree, catalog, trusted))
            if problems:
                self.banners.set(
                    "save",
                    "warning",
                    f"This save already has {len(problems)} problem(s) the game may reject, "
                    f"for example: {problems[0]}",
                )
            for tab in (self.overview, self.story, self.quests):
                tab.load(save, catalog)
            self._show_editor(True)
        self._update_actions()
        self._update_title()

    def _show_editor(self, visible: bool, message: str = "") -> None:
        self.tabs.setVisible(visible)
        self.empty.setVisible(not visible)
        self.empty.setText(message)

    def _edited(self) -> None:
        if self.doc is None:
            return
        self.doc.dirty = True
        for tab in (self.overview, self.story, self.quests):
            tab.refresh()
        self._update_actions()
        self._update_title()

    # ── game running ──────────────────────────────────────────────────────
    def _check_game(self) -> None:
        try:
            running = game_is_running()
        except Exception:  # psutil can fail on locked-down machines
            running = False
        if running == self._game_running:
            return
        self._game_running = running
        if running:
            self.banners.set(
                "game",
                "warning",
                "<b>Dressmaker is running.</b> It autosaves every minute and would overwrite "
                "anything written now, so saving is paused until you close the game.",
            )
        else:
            self.banners.set("game", None)
        self._update_actions()

    # ── saving ────────────────────────────────────────────────────────────
    def _write_args(self) -> dict:
        return {
            "catalog": self.state.catalog,
            "backups": self.state.backups,
            "is_game_running": game_is_running,
        }

    def _run(self, title: str, fn) -> bool:
        """Run a file operation, turning failures into a message box."""
        try:
            fn()
        except InvalidSaveError as exc:
            show_issues(self, title, "The game wouldn't be able to load this save.", exc.issues)
            return False
        except SaveError as exc:
            QMessageBox.warning(self, title, str(exc))
            return False
        except OSError as exc:
            QMessageBox.warning(self, title, f"Couldn't access the file: {exc}")
            return False
        except Exception as exc:
            log.exception("%s failed", title)
            box = QMessageBox(QMessageBox.Icon.Critical, title, f"Something went wrong: {exc}")
            box.setDetailedText(traceback.format_exc())
            box.exec()
            return False
        return True

    def save_document(self) -> bool:
        doc, folder = self.doc, self.state.folder
        if doc is None or folder is None:
            return False

        def write():
            folder.write(doc.slot, doc.save, reason="edit", **self._write_args())

        if not self._run("Save", write):
            return False
        self.statusBar().showMessage(f"Saved {slot_label(doc.slot)}.", 5000)
        self.doc = None
        self.refresh_slots(select=doc.slot)
        return True

    def revert(self) -> None:
        if self.doc is None:
            return
        if self.doc.dirty and not confirm(
            self, "Revert", "Throw away your unsaved changes?", "Revert"
        ):
            return
        slot = self.doc.slot
        self.doc = None
        self._open_slot(slot)

    def _confirm_discard(self) -> bool:
        """Before leaving a slot with unsaved changes: save, discard or stay."""
        if self.doc is None or not self.doc.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"{slot_label(self.doc.slot)} has unsaved changes. Save them?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_document()
        if answer == QMessageBox.StandardButton.Discard:
            slot = self.doc.slot
            self.doc = None
            self._open_slot(slot)  # really drop the edits: reload from disk
            return True
        return False

    # ── import, export, copy, backups ─────────────────────────────────────
    def import_save(self) -> None:
        folder = self.state.folder
        if folder is None or not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import a save",
            str(Path.home()),
            "Dressmaker saves (*.zip *.json);;All files (*)",
        )
        if not path:
            return
        try:
            save, _photos = read_import(Path(path))
        except SaveError as exc:
            QMessageBox.warning(self, "Import", str(exc))
            return
        summary = f"{save.player_name} (rank {save.prestige_level}, {save.gold:,} gold)"
        slot = SlotPickerDialog.ask(
            self,
            folder,
            title="Import a save",
            text=f"Import {summary} into which slot?",
            ok_text="Import",
        )
        if slot is None:
            return
        if self._run("Import", lambda: folder.import_file(Path(path), slot, **self._write_args())):
            self.statusBar().showMessage(f"Imported into {slot_label(slot)}.", 5000)
            self.doc = None
            self.refresh_slots(select=slot)

    def export_save(self) -> None:
        doc, folder = self.doc, self.state.folder
        if doc is None or folder is None:
            return
        if doc.dirty:
            answer = QMessageBox.question(
                self,
                "Export",
                "Export saves the slot as it is on disk. Save your changes first?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Ignore
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save and not self.save_document():
                return
            doc = self.doc
        name = f"Dressmaker {slot_label(doc.slot)} {date.today():%Y-%m-%d}"
        zip_filter = "Save with dress photos (*.zip)"
        json_filter = "Save file only (*.json)"
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export a save", str(Path.home() / f"{name}.zip"), f"{zip_filter};;{json_filter}"
        )
        if not path:
            return
        target = Path(path)
        wanted = ".json" if chosen == json_filter else ".zip"
        if target.suffix.lower() not in (".zip", ".json"):
            target = target.with_suffix(wanted)
        build = self.state.catalog.source.get("build_id") if self.state.catalog else None
        if self._run("Export", lambda: folder.export(doc.slot, target, game_build=build)):
            self.statusBar().showMessage(f"Exported {slot_label(doc.slot)} to {target}.", 8000)

    def copy_slot(self) -> None:
        doc, folder = self.doc, self.state.folder
        if doc is None or folder is None or not self._confirm_discard():
            return
        source = doc.slot
        target = SlotPickerDialog.ask(
            self,
            folder,
            title="Copy slot",
            text=f"Copy {slot_label(source)} into which slot?",
            ok_text="Copy",
            exclude=source,
        )
        if target is None:
            return
        if self._run("Copy slot", lambda: folder.copy_slot(source, target, **self._write_args())):
            self.statusBar().showMessage(
                f"Copied {slot_label(source)} to {slot_label(target)}.", 5000
            )
            self.doc = None
            self.refresh_slots(select=target)

    def show_backups(self) -> None:
        folder = self.state.folder
        if folder is None:
            return
        dialog = BackupsDialog(self, self.state.backups)
        if not dialog.exec() or dialog.chosen is None:
            return
        backup = dialog.chosen
        if not self._confirm_discard():
            return
        slot = SlotPickerDialog.ask(
            self,
            folder,
            title="Restore backup",
            text=f"Restore the backup from {backup.created:%d %b %Y %H:%M} into which slot?",
            ok_text="Restore",
            preferred=backup.slot,
        )
        if slot is None:
            return
        if self._run(
            "Restore",
            lambda: folder.import_file(
                backup.save_path, slot, reason="restore", **self._write_args()
            ),
        ):
            self.statusBar().showMessage(f"Restored into {slot_label(slot)}.", 5000)
            self.doc = None
            self.refresh_slots(select=slot)

    def open_save_folder(self) -> None:
        if self.state.folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.state.folder.path)))

    # ── settings ──────────────────────────────────────────────────────────
    def choose_save_folder(self) -> None:
        if not self._confirm_discard():
            return
        start = str(self.state.folder.path) if self.state.folder else str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Choose Dressmaker's save folder", start)
        if not path:
            return
        has_saves = any((Path(path) / n).exists() for n in ("player.json", "options.json"))
        if not has_saves and not confirm(
            self,
            "Choose save folder",
            "This folder has no Dressmaker saves in it. Use it anyway?",
            "Use folder",
        ):
            return
        self.state.set_save_dir(Path(path))
        self.doc = None
        self.refresh_slots(select_first=True)

    def choose_game_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose the Dressmaker game folder")
        if not path:
            return
        if self.state.set_game_dir(Path(path)) is None:
            QMessageBox.warning(
                self,
                "Choose game folder",
                "That doesn't look like Dressmaker's game folder (it should contain "
                "Dressmaker_Data).",
            )
            return
        self.reread_game_data()

    def about(self) -> None:
        QMessageBox.about(
            self,
            "About Dressmaker Save Editor",
            f"<b>Dressmaker Save Editor</b> {__version__}<br><br>"
            "An unofficial save editor for Dressmaker. Not affiliated with Cozy Lives.<br>"
            f'<a href="{PROJECT_URL}">{PROJECT_URL}</a><br><br>'
            f"Backups: {self.state.backups.root}",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(3000)
            event.accept()
        else:
            event.ignore()
