# Dressmaker Save Editor

Python 3.13 + PySide6 save editor for the Unity game Dressmaker. Read
CONTRIBUTING.md first: it explains the save format and the game's loader, and
every rule there exists because breaking it makes the game discard a save.

- Commands: `uv run pytest`, `uv run pre-commit run --all-files`,
  `uv run python -m dressmaker_editor`, `uv run python scripts/build_exe.py`.
- Never write saves with the `json` module or reformat fixtures: use
  `gamejson.dumps`, which reproduces the game's output byte for byte.
  `tests/fixtures/` and `src/dressmaker_editor/data/*.json` are byte-exact data.
- Every write goes through `SaveFolder.write` (validation, backup, atomic
  replace, `.cache` rebuild). Don't write slot files any other way.
- Anything the editor adds must come from the catalog (`Catalog.ref`,
  `Catalog.enum_ref`); unknown guids make the game reject the save.
- Commit scopes: `core`, `catalog`, `slots`, `story`, `gui`.
