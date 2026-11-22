"""The Side commissions tab: replay or skip side and gift commissions."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..catalog import Catalog
from ..savefile import SaveData
from ..story import QuestRow, Status, Story
from .story_tab import STATUS_COLORS
from .widgets import confirm, humanize

GUID_ROLE = Qt.ItemDataRole.UserRole


class QuestsTab(QWidget):
    edited = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.save: SaveData | None = None
        self.catalog: Catalog | None = None

        self.unlocks = QLabel()
        self.unlocks.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Commission", "Client", "Unlocked after", "Status"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._update_buttons)

        self.replay = QPushButton("Replay (clear progress)")
        self.replay.clicked.connect(self._replay)
        self.complete = QPushButton("Mark as done")
        self.complete.clicked.connect(self._complete)
        self.pay_rewards = QCheckBox("Pay out gold and prestige when marking done")
        buttons = QHBoxLayout()
        buttons.addWidget(self.replay)
        buttons.addWidget(self.complete)
        buttons.addWidget(self.pay_rewards)
        buttons.addStretch()

        help_text = QLabel(
            "Replaying a commission removes it, and the dress you made for it, from your "
            "save. The messenger pigeon brings a side commission again once it is unlocked "
            "and that client has no other commission open. Gold, prestige and friendship "
            "you earned from it are kept. Select several rows to change them at once."
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: palette(placeholder-text);")

        layout = QVBoxLayout(self)
        layout.addWidget(self.unlocks)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        layout.addWidget(help_text)
        self._update_buttons()

    def load(self, save: SaveData, catalog: Catalog) -> None:
        self.save, self.catalog = save, catalog
        self.refresh()

    def refresh(self) -> None:
        if self.save is None or self.catalog is None:
            return
        story = Story(self.save, self.catalog)
        selected = {item.data(0, GUID_ROLE) for item in self.tree.selectedItems()}
        self.tree.clear()
        bold = QFont()
        bold.setBold(True)
        for title, rows in (
            ("Side commissions (messenger pigeon)", story.side_rows()),
            ("Gift commissions", story.gift_rows()),
        ):
            group = QTreeWidgetItem([title])
            group.setFont(0, bold)
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            group.setFirstColumnSpanned(True)
            self.tree.addTopLevelItem(group)
            for row in rows:
                item = self._item(row)
                group.addChild(item)
                if row.guid in selected:
                    item.setSelected(True)
            group.setExpanded(True)
        self.tree.setRootIsDecorated(False)

        notes = []
        for feature, label in (("pigeon", "The messenger pigeon"), ("gifting", "Gifting")):
            unlock = self.catalog.unlock(feature)
            if unlock and not story.feature_unlocked(feature):
                notes.append(
                    f"{label} unlocks after <b>{self.catalog.pretty(unlock['quest'])}</b> "
                    "(see the Story tab)."
                )
        self.unlocks.setText(" ".join(notes))
        self.unlocks.setVisible(bool(notes))
        self._update_buttons()

    def _item(self, row: QuestRow) -> QTreeWidgetItem:
        status = row.status.value
        if row.status is Status.COMPLETED and row.outcome and row.outcome != "Succeeded":
            status = f"{status} ({humanize(row.outcome).lower()})"
        unlocked_by = self.catalog.pretty(row.unlocked_by) if row.unlocked_by else ""
        item = QTreeWidgetItem([row.title, row.client, unlocked_by, status])
        item.setData(0, GUID_ROLE, row.guid)
        item.setData(3, GUID_ROLE, row.status)
        item.setForeground(3, QBrush(QColor(STATUS_COLORS[row.status])))
        return item

    def _selected(self) -> list[tuple[str, Status]]:
        return [
            (item.data(0, GUID_ROLE), item.data(3, GUID_ROLE))
            for item in self.tree.selectedItems()
            if item.data(0, GUID_ROLE)
        ]

    def _update_buttons(self) -> None:
        selected = self._selected()
        self.replay.setEnabled(any(s in (Status.COMPLETED, Status.ACTIVE) for _g, s in selected))
        self.complete.setEnabled(any(s is not Status.COMPLETED for _g, s in selected))

    def _replay(self) -> None:
        targets = [g for g, s in self._selected() if s in (Status.COMPLETED, Status.ACTIVE)]
        if not targets:
            return
        names = ", ".join(self.catalog.pretty(g) for g in targets[:5])
        more = f" and {len(targets) - 5} more" if len(targets) > 5 else ""
        if not confirm(
            self,
            "Replay commissions",
            f"Clear the progress of {names}{more}?",
            "Clear progress",
            "The dresses you made for them are removed from your sketchbook. "
            "Nothing is written until you save.",
        ):
            return
        story = Story(self.save, self.catalog)
        for guid in targets:
            story.reset(guid)
        self.edited.emit()

    def _complete(self) -> None:
        targets = [g for g, s in self._selected() if s is not Status.COMPLETED]
        if not targets:
            return
        story = Story(self.save, self.catalog)
        for guid in targets:
            story.complete(guid, pay_rewards=self.pay_rewards.isChecked())
        self.edited.emit()
