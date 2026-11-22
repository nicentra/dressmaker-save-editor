"""Dialogs: picking a target slot, browsing backups, showing problems."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..catalog import Catalog
from ..checks import fix_all, story_issues
from ..savefile import SaveData
from ..slots import SLOT_COUNT, Backup, BackupStore, SaveFolder, slot_label
from ..validate import ERROR, Issue


def describe_slot(folder: SaveFolder, slot: int) -> str:
    info = folder.slot_info(slot)
    if not info.exists:
        return f"{info.label}: empty"
    bits = [info.player_name or "Dressmaker"]
    if info.prestige is not None:
        bits.append(f"rank {min(50, int(info.prestige))}")
    if info.modified:
        bits.append(f"saved {info.modified:%d %b %Y %H:%M}")
    return f"{info.label}: " + ", ".join(bits)


class SlotPickerDialog(QDialog):
    """Ask which slot to write to, warning about the ones that get replaced."""

    def __init__(
        self,
        parent: QWidget,
        folder: SaveFolder,
        title: str,
        text: str,
        ok_text: str,
        exclude: int | None = None,
        preferred: int | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.folder = folder
        layout = QVBoxLayout(self)
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.group = QButtonGroup(self)
        first_choice = None
        for slot in range(SLOT_COUNT):
            button = QRadioButton(describe_slot(folder, slot))
            if slot == exclude:
                button.setEnabled(False)
                button.setText(button.text() + " (this slot)")
            self.group.addButton(button, slot)
            layout.addWidget(button)
            if slot != exclude and first_choice is None and not folder.exists(slot):
                first_choice = slot
        choice = preferred if preferred not in (None, exclude) else first_choice
        if choice is None:
            choice = next(s for s in range(SLOT_COUNT) if s != exclude)
        self.group.button(choice).setChecked(True)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #8a1c17;")
        layout.addWidget(self.warning)
        self.group.idToggled.connect(lambda *_: self._update_warning())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(ok_text, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_warning()

    def _update_warning(self) -> None:
        slot = self.selected
        if slot is not None and self.folder.exists(slot):
            self.warning.setText(
                f"{slot_label(slot)} will be replaced. A backup of it is kept and can be "
                "restored from Backups."
            )
        else:
            self.warning.clear()

    @property
    def selected(self) -> int | None:
        slot = self.group.checkedId()
        return slot if slot >= 0 else None

    @classmethod
    def ask(cls, parent: QWidget, folder: SaveFolder, **kwargs) -> int | None:
        dialog = cls(parent, folder, **kwargs)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.selected
        return None


class BackupsDialog(QDialog):
    """Lists the editor's backups; the caller restores the chosen one."""

    def __init__(self, parent: QWidget, store: BackupStore):
        super().__init__(parent)
        self.setWindowTitle("Backups")
        self.resize(720, 420)
        self.store = store
        self.chosen: Backup | None = None
        self.backups = store.list()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "The editor saves a copy of a slot every time it is about to overwrite it. "
            "Restoring puts a copy back into a slot of your choice."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(self.backups), 5)
        self.table.setHorizontalHeaderLabels(["Taken", "From", "Name", "Rank", "Before"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        reasons = {"edit": "Saving changes", "import": "Import"}
        for row, backup in enumerate(self.backups):
            rank = "" if backup.prestige is None else str(min(50, int(backup.prestige)))
            reason = reasons.get(backup.reason, backup.reason.replace("-", " ").capitalize())
            for column, text in enumerate(
                (
                    f"{backup.created:%d %b %Y %H:%M:%S}",
                    slot_label(backup.slot),
                    backup.player_name or "",
                    rank,
                    reason,
                )
            ):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.itemSelectionChanged.connect(self._update)
        self.table.cellDoubleClicked.connect(lambda *_: self._restore())
        layout.addWidget(self.table, 1)
        if not self.backups:
            layout.addWidget(QLabel("No backups yet."))

        buttons = QHBoxLayout()
        self.restore_button = QPushButton("Restore…")
        self.restore_button.clicked.connect(self._restore)
        open_folder = QPushButton("Open backups folder")
        open_folder.clicked.connect(self._open_folder)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(open_folder)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._update()

    def _update(self) -> None:
        self.restore_button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def _restore(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self.chosen = self.backups[rows[0].row()]
            self.accept()

    def _open_folder(self) -> None:
        self.store.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))


def show_issues(parent: QWidget, title: str, text: str, issues: list[Issue]) -> None:
    issues = sorted(issues, key=lambda issue: issue.severity != ERROR)  # errors first
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(
        "\n".join(f"• {issue}" for issue in issues[:8])
        + (f"\n…and {len(issues) - 8} more" if len(issues) > 8 else "")
    )
    box.setDetailedText("\n".join(str(issue) for issue in issues))
    box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    box.exec()


class StoryCheckDialog(QDialog):
    """Lists story problems in a save, each with a button that fixes it."""

    def __init__(self, parent: QWidget, save: SaveData, catalog: Catalog):
        super().__init__(parent)
        self.setWindowTitle("Story check")
        self.resize(640, 420)
        self.save, self.catalog = save, catalog
        self.changed = False

        layout = QVBoxLayout(self)
        intro = QLabel(
            "These don't stop the game from loading the save, but normal play can't get "
            "there. Fixes change the save in the editor; nothing is written until you save."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self.fix_all_button = QPushButton("Fix all")
        self.fix_all_button.clicked.connect(self._fix_all)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.fix_all_button)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._rebuild()

    def _rebuild(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        issues = story_issues(self.save, self.catalog)
        if not issues:
            self.list_layout.addWidget(QLabel("Everything looks consistent."))
        for issue in issues:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(issue.message)
            label.setWordWrap(True)
            button = QPushButton(issue.fix_label)
            button.clicked.connect(lambda _=False, fix=issue.fix: self._apply(fix))
            row_layout.addWidget(label, 1)
            row_layout.addWidget(button)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch()
        self.fix_all_button.setEnabled(bool(issues))

    def _apply(self, fix) -> None:
        fix()
        self.changed = True
        self._rebuild()

    def _fix_all(self) -> None:
        if fix_all(self.save, self.catalog):
            self.changed = True
        self._rebuild()
