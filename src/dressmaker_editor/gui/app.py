"""Start the editor: logging, crash reporting, the Qt application."""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import __version__
from ..app_state import AppState
from .main_window import MainWindow
from .widgets import app_icon

log = logging.getLogger("dressmaker_editor")


def _setup_logging(state: AppState) -> None:
    handlers: list[logging.Handler] = []
    try:
        state.data_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                state.data_dir / "editor.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
            )
        )
    except OSError:
        pass
    if sys.stderr is not None:  # windowed builds have no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _excepthook(exc_type, exc, tb) -> None:
    log.error("unhandled error", exc_info=(exc_type, exc, tb))
    if QApplication.instance() is not None:
        box = QMessageBox(
            QMessageBox.Icon.Critical,
            "Dressmaker Save Editor",
            f"Something went wrong: {exc}\n\nNothing was written to your saves.",
        )
        box.setDetailedText("".join(traceback.format_exception(exc_type, exc, tb)))
        box.exec()


def run(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Dressmaker Save Editor")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    state = AppState()
    _setup_logging(state)
    log.info("Dressmaker Save Editor %s starting", __version__)
    sys.excepthook = _excepthook
    window = MainWindow(state)
    window.show()
    return app.exec()
