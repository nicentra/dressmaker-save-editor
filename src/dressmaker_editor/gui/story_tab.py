"""The Story tab: jump through the main story and edit story flags."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import gamejson
from ..catalog import Catalog
from ..savefile import SaveData, by_member
from ..story import QuestRow, Status, Story
from .widgets import confirm, humanize

STATUS_COLORS = {
    Status.COMPLETED: "#2e7d32",
    Status.ACTIVE: "#1565c0",
    Status.AVAILABLE: "#8d5a00",
    Status.LOCKED: "#757575",
    Status.UPCOMING: "#757575",
}


def status_item(row: QuestRow) -> QTableWidgetItem:
    text = row.status.value
    if row.status is Status.COMPLETED and row.outcome and row.outcome != "Succeeded":
        text = f"{text} ({humanize(row.outcome).lower()})"
    if row.status is Status.AVAILABLE and row.kind in ("main", "final"):
        text = "Next up"
    item = QTableWidgetItem(text)
    item.setForeground(QBrush(QColor(STATUS_COLORS[row.status])))
    return item


class StoryTab(QWidget):
    edited = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.save: SaveData | None = None
        self.catalog: Catalog | None = None
        self._loading = False

        self.position = QLabel()
        self.position.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.position.setWordWrap(True)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Commission", "Client", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self._jump_to_selected())

        self.jump_button = QPushButton("Continue the story from here")
        self.jump_button.setEnabled(False)
        self.jump_button.clicked.connect(self._jump_to_selected)
        self.finish_button = QPushButton("Mark the whole story as finished")
        self.finish_button.clicked.connect(self._finish)
        self.pay_rewards = QCheckBox("Pay out the gold and prestige of commissions marked done")
        buttons = QHBoxLayout()
        buttons.addWidget(self.jump_button)
        buttons.addWidget(self.finish_button)
        buttons.addStretch()

        help_text = QLabel(
            "Pick a commission and continue from it: every main commission before it is "
            "marked done (with a plain white stand-in dress), and it and everything after "
            "it are cleared so the front desk offers them again. Side commissions are not "
            "touched."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: palette(placeholder-text);")

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.position)
        main_layout.addWidget(self.table, 1)
        main_layout.addLayout(buttons)
        main_layout.addWidget(self.pay_rewards)
        main_layout.addWidget(help_text)

        self.flags_box = QGroupBox("Story flags")
        self.flags_layout = QVBoxLayout(self.flags_box)
        self.variables_box = QGroupBox("Story variables")
        self.variables_layout = QFormLayout(self.variables_box)
        flags_hint = QLabel(
            "Flags and variables are set by choices in conversations. Changing them "
            "changes what characters remember."
        )
        flags_hint.setWordWrap(True)
        flags_hint.setStyleSheet("color: palette(placeholder-text);")
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.flags_box)
        side_layout.addWidget(self.variables_box)
        side_layout.addWidget(flags_hint)
        side_layout.addStretch()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(main)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._flag_boxes: dict[str, QCheckBox] = {}
        self._variable_boxes: dict[str, QDoubleSpinBox] = {}

    # ── loading ───────────────────────────────────────────────────────────
    def load(self, save: SaveData, catalog: Catalog) -> None:
        rebuild = catalog is not self.catalog
        self.save, self.catalog = save, catalog
        if rebuild:
            self._build_flags()
        self.table.clearSelection()  # start at the new save's position
        self.refresh()

    def _build_flags(self) -> None:
        for box in self._flag_boxes.values():
            self.flags_layout.removeWidget(box)
            box.setParent(None)
            box.deleteLater()
        for spin in self._variable_boxes.values():
            self.variables_layout.removeRow(spin)
        self._flag_boxes.clear()
        self._variable_boxes.clear()
        for name in self.catalog.enum("StoryFlag"):
            box = QCheckBox(humanize(name))
            box.toggled.connect(lambda checked, n=name: self._flag_toggled(n, checked))
            self.flags_layout.addWidget(box)
            self._flag_boxes[name] = box
        for name in self.catalog.enum("StoryVariable"):
            spin = QDoubleSpinBox()
            spin.setRange(-9999, 9999)
            spin.setDecimals(2)
            spin.valueChanged.connect(lambda value, n=name: self._variable_changed(n, value))
            self.variables_layout.addRow(humanize(name), spin)
            self._variable_boxes[name] = spin

    def refresh(self) -> None:
        if self.save is None or self.catalog is None:
            return
        story = Story(self.save, self.catalog)
        position = story.position()
        self.position.setText(story.position_label())
        selected = self._selected_index()
        rows = story.main_rows()
        self._loading = True
        try:
            self.table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                number = QTableWidgetItem("" if row.kind == "final" else str(row.index + 1))
                number.setData(Qt.ItemDataRole.UserRole, row.index)
                number.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                title = QTableWidgetItem(row.title + (" (final)" if row.kind == "final" else ""))
                self.table.setItem(i, 0, number)
                self.table.setItem(i, 1, title)
                self.table.setItem(i, 2, QTableWidgetItem(row.client))
                self.table.setItem(i, 3, status_item(row))
            target = selected if selected is not None else min(position, len(rows) - 1)
            if 0 <= target < len(rows):
                self.table.selectRow(target)
                self.table.scrollToItem(self.table.item(target, 0))
            for name, box in self._flag_boxes.items():
                box.setChecked(self.save.story_flag(name))
            for name, spin in self._variable_boxes.items():
                spin.setValue(self.save.numeric_variable(name))
        finally:
            self._loading = False
        self.finish_button.setEnabled(position < story.finished_position)
        self._selection_changed()

    def _selected_index(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else None

    def _selection_changed(self) -> None:
        index = self._selected_index()
        if index is None or self.save is None:
            self.jump_button.setEnabled(False)
            return
        story = Story(self.save, self.catalog)
        current = min(story.position(), len(self.catalog.main_quests))
        self.jump_button.setEnabled(index != current)
        is_final = index >= len(self.catalog.main_quests)
        self.jump_button.setText(
            "Continue from the final commission" if is_final else "Continue the story from here"
        )

    # ── actions ───────────────────────────────────────────────────────────
    def _jump_to_selected(self) -> None:
        index = self._selected_index()
        if index is not None:
            self._jump(index)

    def _finish(self) -> None:
        self._jump(Story(self.save, self.catalog).finished_position)

    def _jump(self, position: int) -> None:
        story = Story(self.save, self.catalog)
        preview = Story(self.save.copy(), self.catalog).jump_to(
            position, pay_rewards=self.pay_rewards.isChecked()
        )
        if not preview:
            return
        details = []
        if preview.completed:
            details.append(
                f"{len(preview.completed)} commission(s) will be marked done with a stand-in dress."
            )
        if preview.removed:
            details.append(
                f"{len(preview.removed)} commission(s) will be cleared, and the dresses you made "
                "for them removed from your sketchbook."
            )
        if preview.gold or preview.prestige:
            details.append(f"You'll get {preview.gold:,} gold and {preview.prestige:.2f} prestige.")
        target = story.position_label(position)
        if not confirm(
            self,
            "Change story progress",
            f"Continue from: {target}?",
            "Change progress",
            "\n\n".join(details) + "\n\nNothing is written until you save.",
        ):
            return
        story.jump_to(position, pay_rewards=self.pay_rewards.isChecked())
        self.table.clearSelection()
        self.edited.emit()

    def _flag_toggled(self, name: str, checked: bool) -> None:
        if self._loading or self.save is None:
            return
        key = self.catalog.enum_ref("StoryFlag", name)
        self.save.story_flags.set(key, checked, by_member(name))
        self.edited.emit()

    def _variable_changed(self, name: str, value: float) -> None:
        if self._loading or self.save is None:
            return
        key = self.catalog.enum_ref("StoryVariable", name)
        self.save.numeric_variables.set(key, gamejson.to_float32(value), by_member(name))
        self.edited.emit()
