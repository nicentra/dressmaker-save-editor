"""Detect a running copy of the game."""

from __future__ import annotations

import psutil

GAME_PROCESS_NAMES = {"dressmaker.exe", "dressmaker"}


def game_is_running() -> bool:
    """Whether Dressmaker is running (natively or under Proton)."""
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in GAME_PROCESS_NAMES:
            return True
    return False
