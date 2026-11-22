"""Regenerate the game data that ships with the editor.

Needs Dressmaker installed (and, for the placeholder dress, a save with at
least one finished dress). Run after a game update:

    uv run python scripts/update_snapshot.py
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from dressmaker_editor import gamejson
from dressmaker_editor.extract import extract_catalog
from dressmaker_editor.paths import find_game_install, find_save_dir, game_install_at
from dressmaker_editor.savefile import SaveData
from dressmaker_editor.slots import SLOT_COUNT, SaveFolder
from dressmaker_editor.story import PLACEHOLDER_FABRIC

DATA = Path(__file__).resolve().parent.parent / "src" / "dressmaker_editor" / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, help="game folder (default: find it through Steam)")
    parser.add_argument("--saves", type=Path, help="save folder (default: the game's)")
    args = parser.parse_args()

    install = game_install_at(args.game) if args.game else find_game_install()
    if install is None:
        print("Dressmaker isn't installed (or pass --game)", file=sys.stderr)
        return 1
    catalog = extract_catalog(install, progress=print)
    catalog.save(DATA / "catalog.json")
    print(f"wrote {DATA / 'catalog.json'}: {len(catalog.assets)} assets, build {install.build_id}")

    saves = args.saves or find_save_dir()
    if saves is None:
        print("no save folder; placeholder dress not updated", file=sys.stderr)
        return 0
    folder = SaveFolder(saves)
    dresses = []
    for slot in range(SLOT_COUNT):
        if not folder.exists(slot):
            continue
        save: SaveData = folder.read(slot)
        for state in save.completed_quests:
            dress = state.get("dress") if isinstance(state, dict) else None
            if isinstance(dress, dict) and dress.get("finished") and dress.get("bodiceDefinition"):
                dresses.append(dress)
    if not dresses:
        print("no finished dress found; placeholder dress not updated", file=sys.stderr)
        return 0
    # The simplest dress makes the smallest, least surprising template.
    dress = copy.deepcopy(
        min(dresses, key=lambda d: (len(d.get("panelStates") or []), len(gamejson.dumps(d))))
    )
    fabric = catalog.find("Fabric", PLACEHOLDER_FABRIC)
    for panel in dress.get("panelStates") or []:
        if fabric:
            panel["fabric"] = catalog.ref(fabric["guid"])
        panel["cutoutActive"] = False
    dress["accessoryStates"] = []
    (DATA / "placeholder_dress.json").write_bytes(gamejson.dumps(dress).encode("utf-8"))
    print(f"wrote {DATA / 'placeholder_dress.json'} ({len(dress.get('panelStates') or [])} panels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
