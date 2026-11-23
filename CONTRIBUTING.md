# Contributing

## Setup

The project runs on Python 3.15 (uv installs it). UnityPy and lz4 have no
3.15 wheels yet, so `uv sync` compiles them: on Windows that needs the Visual
Studio Build Tools with the C++ workload. CI has them built in.

```sh
uv sync
uv run pre-commit install      # ruff, workflow linters, typos …; pytest on push
uv run python -m dressmaker_editor
```

Tests run anywhere. Tests marked `game` also run against your own Dressmaker
install and saves when they exist: they round-trip every real save byte for
byte. Set `QT_QPA_PLATFORM=offscreen` to run the GUI tests without a display.

Commits and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/).
PRs are squash-merged, so the PR title becomes the commit on `main`.

## Versions and releases

Versions follow [Semantic Versioning](https://semver.org/), and nobody sets
them by hand: release-please reads the commits on `main` and keeps a release PR
open with the next version and its changelog.

| Commit | Version change | For this app that means |
|---|---|---|
| `fix:`, `perf:` | patch, 1.0.0 → 1.0.1 | a bug fix, nothing new to learn |
| `feat:` | minor, 1.0.0 → 1.1.0 | something new you can do |
| `feat!:` / `BREAKING CHANGE:` | major, 1.0.0 → 2.0.0 | something that used to work no longer does, e.g. older exports or backups can't be imported, a feature is removed, or settings move |
| `docs:`, `chore:`, `ci:`, `test:`, `refactor:`, `build:` | none | |

Merging the release PR tags `vX.Y.Z`, publishes the GitHub release and attaches
`DressmakerSaveEditor.exe`. Every merge to `main` also replaces the `dev`
pre-release; those builds report `X.Y.Z+dev.<commit>` (semver build metadata),
so a development build never passes for a release. The version is shown in
Help → About and in the executable's file properties.

## Layout

| Module | Does |
|---|---|
| `gamejson.py` | Reads and writes the game's JSON dialect, byte-exact |
| `savefile.py` | `SaveData`: typed access to the fields the editor changes |
| `validate.py` | Rejects anything the game's loader would reject |
| `catalog.py`, `extract.py`, `gamedata.py` | Game data: items, characters, quests, the campaign |
| `slots.py` | Slots on disk: safe writes, copy, import/export, backups |
| `story.py` | Story position, jumping, replaying and completing quests |
| `gui/` | PySide6 window |

After a game update, refresh the bundled fallback data with
`uv run python scripts/update_snapshot.py` (needs the game installed).

## The save format

Dressmaker is a Unity (Mono) game, so all of this comes from its decompiled
`Assembly-CSharp.dll` and `Framework.dll`.

- Slot 1 is `player.json`, slots 2 and 3 are `player1.json` and `player2.json`,
  all in `AppData\LocalLow\Cozy Lives\Dressmaker`. Each has a `.cache` (what the
  slot menu shows) and a `.lastgood` copy. Dress photos are
  `thumbnails/<guid>.png`, shared by all slots.
- JSON written by `Framework.JSONObject`: tab indentation, `" : "` separators,
  nested containers on their own line, LF line endings, no BOM.
- Game assets are referenced as `"<32 hex guid>,<asset name>"`; only the guid is
  read. C# enums are `"<Name>,<int>"` (the name wins). Vectors are `"x,y,z"`
  strings. Dictionaries are `{"@Keys": [...], "@Values": [...]}`.
- Floats are written as integer part plus fraction, each with Mono's 7
  significant digits (see `gamejson.format_float`), never with an exponent.

What the loader does with that, and why the editor is careful:

- A number containing `.` is a float, anything else goes through `int.Parse`.
  An `int` field holding `1.0`, or any number with an exponent, fails.
- Strings are never unescaped, so `"`, `\` or control characters break a save.
- An unknown asset guid throws. Any failure makes the game copy the file to
  `.corrupt` and load `.lastgood` instead, and its next autosave overwrites the
  edit. A successful load copies the save over `.lastgood`.
- `ValidateAndRepair` drops fabrics without a material and accessories without
  a prefab, then re-applies prestige (max rank 50) and friendship (max 5)
  unlocks.

Story:

- The campaign (`FullCampaign` in `sharedassets2.assets`) lists the main
  quests in order and the side quests. The front desk offers the first main
  quest that is neither completed nor active; after all of them comes
  `FinalQuest`.
- Side quests come by messenger pigeon once their storylet requirements are
  met and the client has no other open commission. The pigeon offers each
  client only the first of their side quests in the campaign's `sideQuests`
  list that is unlocked and not done, so a client's side quests come one at a
  time. The editor treats them as a chain (`Story.client_side_quests`):
  replaying one clears the later ones, marking one done completes the earlier
  ones. Gift quests unlock with
  gifting. Unlocks: gifting after VivianArtShow, freeform dresses after
  PriyaFuneral, the pigeon after DorothyBerty (in build 26043449).
- A quest is done when `completedQuests` has an entry for it. Completed
  entries need a `dress`: the sketchbook's dress history and the ending
  credits load it and crash on `null`.
- Handing in a commission (`FrontDesk.CompleteQuest`) pays +1 friendship with
  the client and, except for gifts, its `budget` in gold and
  `6 / (6 + dresses so far)` prestige. A failed one pays the budget (except
  for gifts), +0.25 friendship and a quarter of the prestige.

Game data extraction: `resources.assets` has stripped type trees, so
TypeTreeGeneratorAPI rebuilds them from the DLLs. Skip the `Yarn.System*` and
`Yarn.Microsoft*` DLLs (duplicate assembly names). Quest requirements are
`[SerializeReference]` fields; `extract.py` teaches UnityPy to read them.
