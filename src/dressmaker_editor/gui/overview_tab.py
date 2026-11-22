"""The Overview tab: name, gold and prestige."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import gamejson
from ..catalog import Catalog
from ..savefile import MAX_PRESTIGE_LEVEL, SaveData
from ..story import Story


class SafeTextValidator(QValidator):
    """Refuse characters the game's JSON reader can't cope with."""

    def validate(self, text: str, pos: int):
        if gamejson.is_valid_string(text):
            return QValidator.State.Acceptable, text, pos
        return QValidator.State.Invalid, text, pos


class OverviewTab(QWidget):
    edited = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.save: SaveData | None = None
        self.catalog: Catalog | None = None
        self._loading = False

        self.name = QLineEdit()
        self.name.setValidator(SafeTextValidator(self))
        self.name.setMaxLength(64)
        self.name.textEdited.connect(self._name_edited)

        self.gold = QSpinBox()
        self.gold.setRange(0, gamejson.INT_MAX)
        self.gold.setGroupSeparatorShown(True)
        self.gold.setMinimumWidth(160)
        self.gold.valueChanged.connect(self._gold_changed)
        gold_row = QHBoxLayout()
        gold_row.addWidget(self.gold)
        for amount in (1_000, 10_000, 100_000):
            button = QPushButton(f"+{amount:,}")
            button.clicked.connect(
                lambda _=False, a=amount: self.gold.setValue(self.gold.value() + a)
            )
            gold_row.addWidget(button)
        gold_row.addStretch()

        self.prestige = QDoubleSpinBox()
        self.prestige.setRange(0, 9999)
        self.prestige.setDecimals(2)
        self.prestige.setSingleStep(1)
        self.prestige.setMinimumWidth(160)
        self.prestige.valueChanged.connect(self._prestige_changed)
        self.rank = QLabel()
        max_rank = QPushButton(f"Max rank ({MAX_PRESTIGE_LEVEL})")
        max_rank.clicked.connect(
            lambda: self.prestige.setValue(max(self.prestige.value(), MAX_PRESTIGE_LEVEL))
        )
        prestige_row = QHBoxLayout()
        prestige_row.addWidget(self.prestige)
        prestige_row.addWidget(max_rank)
        prestige_row.addWidget(self.rank)
        prestige_row.addStretch()

        player = QGroupBox("Player")
        form = QFormLayout(player)
        form.addRow("Name", self.name)
        form.addRow("Gold", gold_row)
        form.addRow("Prestige", prestige_row)
        hint = QLabel(
            "Your rank is the whole part of your prestige, up to 50. Fabrics and "
            "accessories for every rank you reach are unlocked the next time the "
            "game loads the save."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        form.addRow(hint)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        progress = QGroupBox("Progress")
        QVBoxLayout(progress).addWidget(self.summary)

        layout = QVBoxLayout(self)
        layout.addWidget(player)
        layout.addWidget(progress)
        layout.addStretch()

    def load(self, save: SaveData, catalog: Catalog) -> None:
        self.save, self.catalog = save, catalog
        self.refresh()

    def refresh(self) -> None:
        if self.save is None:
            return
        self._loading = True
        try:
            if self.name.text() != self.save.player_name:
                self.name.setText(self.save.player_name)
            self.gold.setValue(self.save.gold)
            self.prestige.setValue(self.save.prestige)
        finally:
            self._loading = False
        self._update_labels()

    def _update_labels(self) -> None:
        save, catalog = self.save, self.catalog
        level = save.prestige_level
        self.rank.setText(f"Rank {level} of {MAX_PRESTIGE_LEVEL}")
        lines = []
        if catalog is not None:
            story = Story(save, catalog)
            lines.append(f"<b>Story:</b> {story.position_label()}")
            side = story.side_rows()
            done = sum(1 for r in side if r.status.name == "COMPLETED")
            lines.append(f"<b>Side commissions done:</b> {done} of {len(side)}")
            dresses = sum(
                1
                for s in save.completed_quests
                if isinstance(s, dict)
                and s.get("questDefinition")
                and s["questDefinition"][:32] not in catalog.gift_quests
            )
            lines.append(f"<b>Dresses made:</b> {dresses}")
        lines.append(f"<b>Commissions in progress:</b> {len(save.active_commissions)}")
        self.summary.setText("<br>".join(lines))

    def _name_edited(self, text: str) -> None:
        if self._loading or self.save is None or not text.strip():
            return
        self.save.player_name = text
        self.edited.emit()

    def _gold_changed(self, value: int) -> None:
        if self._loading or self.save is None:
            return
        self.save.gold = value
        self.edited.emit()

    def _prestige_changed(self, value: float) -> None:
        if self._loading or self.save is None:
            return
        self.save.prestige = value
        self._update_labels()
        self.edited.emit()
