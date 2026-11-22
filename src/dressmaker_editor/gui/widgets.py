"""Small building blocks shared by the editor's tabs and dialogs."""

from __future__ import annotations

import re
from importlib import resources

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..story import Status

# (background, text) for light and dark themes.
BANNER_COLORS = {
    "info": (("#e8f1fb", "#1d4f7a"), ("#1c3247", "#cfe3f7")),
    "warning": (("#fff4d6", "#6b4a00"), ("#3d3115", "#ffe3a3")),
    "error": (("#fde2e1", "#8a1c17"), ("#4a1f1d", "#ffcdc9")),
}
STATUS_COLORS = {  # light, dark
    Status.COMPLETED: ("#2e7d32", "#81c784"),
    Status.ACTIVE: ("#1565c0", "#64b5f6"),
    Status.AVAILABLE: ("#8d5a00", "#ffb74d"),
    Status.LOCKED: ("#757575", "#9e9e9e"),
    Status.UPCOMING: ("#757575", "#9e9e9e"),
}


def is_dark() -> bool:
    """Whether the app runs with a dark colour scheme (Windows dark mode)."""
    app = QApplication.instance()
    palette = app.palette() if app is not None else QPalette()
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def status_brush(status: Status) -> QBrush:
    return QBrush(QColor(STATUS_COLORS[status][1 if is_dark() else 0]))


def app_icon() -> QIcon:
    data = resources.files("dressmaker_editor.data").joinpath("icon.png").read_bytes()
    pixmap = QPixmap()
    pixmap.loadFromData(data)
    return QIcon(pixmap)


def humanize(name: str) -> str:
    """``MetEdith`` → ``Met Edith``, ``PoeticStanza1`` → ``Poetic Stanza 1``.

    Capitals are kept because the words are often character names.
    """
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", name)
    return " ".join(words) if words else name


class Banner(QFrame):
    """A coloured strip with a message, hidden while it has nothing to say."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setOpenExternalLinks(False)
        self.label.linkActivated.connect(self._link)
        self.on_link = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.addWidget(self.label)
        self.hide()

    def show_message(self, kind: str, html: str) -> None:
        background, text = BANNER_COLORS[kind][1 if is_dark() else 0]
        self.setStyleSheet(
            f"Banner {{ background: {background}; border-radius: 6px; }}QLabel {{ color: {text}; }}"
        )
        self.label.setText(html)
        self.show()

    def clear(self) -> None:
        self.label.clear()
        self.hide()

    def _link(self, href: str) -> None:
        if self.on_link is not None:
            self.on_link(href)


class BannerStack(QWidget):
    """Several independent banners stacked on top of each other."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._banners: dict[str, Banner] = {}

    def set(self, key: str, kind: str | None, html: str = "", on_link=None) -> None:
        banner = self._banners.get(key)
        if banner is None:
            banner = Banner(self)
            self._banners[key] = banner
            self._layout.addWidget(banner)
        banner.on_link = on_link
        if kind is None:
            banner.clear()
        else:
            banner.show_message(kind, html)


def placeholder_pixmap(size: int, text: str = "No photo") -> QPixmap:
    background, ink = ("#3a3532", "#a89c92") if is_dark() else ("#f3ece6", "#9a8b80")
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(background))
    painter = QPainter(pixmap)
    painter.setPen(QColor(ink))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pixmap


def confirm(parent: QWidget, title: str, text: str, ok: str, detail: str | None = None) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    ok_button = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(ok_button)
    box.exec()
    return box.clickedButton() is ok_button
