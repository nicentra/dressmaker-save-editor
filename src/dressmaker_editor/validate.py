"""Check a save the way the game's loader will, before it is written.

The game rejects a whole save over a single problem (an unknown asset guid, an
``int`` field holding ``1.0``, a quote inside a string) and silently falls back
to its previous backup, so every problem it would choke on is an error here.
Problems the game quietly repairs on load are warnings.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from . import gamejson
from .catalog import Catalog
from .gamejson import GameFloat
from .savefile import ref_guid

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


# Top-level fields whose C# type is int: a JSON float there breaks the load.
_INT_FIELDS = ("FILE_FORMAT", "gold", "activeCommissionIndex")
_NUMBER_FIELDS = ("prestige",)
_REF_LISTS = {
    "unlockedPatterns": "GarmentComponent",
    "unlockedFabrics": "Fabric",
    "unlockedAccessories": "AccessoryDefinition",
    "shownGossipReports": "GossipReportDefinition",
    "newFabrics": "Fabric",
    "newAccessories": "AccessoryDefinition",
}
_REF_KEYED_DICTS = {
    "ownedAccessories": "AccessoryDefinition",
    "characterRelationships": "CharacterDefinition",
    "characterMeasurementsCm": "CharacterDefinition",
}
_ENUM_KEYED_DICTS = {"storyFlags": "StoryFlag", "numericVariables": "StoryVariable"}
_QUEST_STATE_REFS = {
    "questDefinition": "QuestDefinition",
    "dressRecipient": "CharacterDefinition",
    "previewBodice": "BodiceDefinition",
    "previewCollar": "CollarDefinition",
    "previewSleeve": "SleeveDefinition",
    "previewSkirt": "SkirtDefinition",
}
_QUEST_STATE_INTS = (
    "previewBodiceVariant",
    "previewCollarVariant",
    "previewSleeveVariant",
    "previewSkirtVariant",
)
_OUTCOME_ENUM = "QuestDefinition/QuestOutcome"


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == ERROR]


def validate(
    tree: Any, catalog: Catalog | None, trusted: frozenset[str] = frozenset()
) -> list[Issue]:
    """List everything the game would reject.

    ``trusted`` guids count as known even if the catalog lacks them; pass the
    guids the save already had when the catalog may be older than the game.
    """
    issues: list[Issue] = []
    if not isinstance(tree, dict):
        return [Issue(ERROR, "(file)", "a save file must be a JSON object")]

    for path, value in _walk(tree, ""):
        issues.extend(_check_value(path, value, catalog, trusted))

    for name in _INT_FIELDS:
        if name in tree and not _is_int(tree[name]):
            issues.append(Issue(ERROR, name, "must be a whole number"))
    for name in _NUMBER_FIELDS:
        if name in tree and not _is_number(tree[name]):
            issues.append(Issue(ERROR, name, "must be a number"))
    if "playerName" in tree and not isinstance(tree["playerName"], (str, type(None))):
        issues.append(Issue(ERROR, "playerName", "must be text"))

    if catalog is not None:
        issues.extend(_check_types(tree, catalog))
    return issues


def _walk(value: Any, path: str) -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            if not gamejson.is_valid_string(key):
                yield f"{path}.{key!r}", _BadKey(key)
            yield from _walk(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _walk(item, f"{path}[{i}]")


class _BadKey(str):
    pass


def _check_value(
    path: str, value: Any, catalog: Catalog | None, trusted: frozenset[str]
) -> Iterator[Issue]:
    if isinstance(value, _BadKey):
        yield Issue(
            ERROR, path, "field names can't contain quotes, backslashes or control characters"
        )
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, GameFloat):
        if "e" in value.raw.lower():
            yield Issue(ERROR, path, f"the game can't read numbers with exponents ({value.raw})")
    elif isinstance(value, int):
        if not gamejson.INT_MIN <= value <= gamejson.INT_MAX:
            yield Issue(ERROR, path, "number is too large for the game")
    elif isinstance(value, float):
        if not math.isfinite(value):
            yield Issue(ERROR, path, "number must be finite")
    elif isinstance(value, str):
        if not gamejson.is_valid_string(value):
            yield Issue(ERROR, path, "text can't contain quotes, backslashes or control characters")
        guid = ref_guid(value)
        if guid and catalog is not None and guid not in catalog and guid not in trusted:
            yield Issue(
                ERROR,
                path,
                f"unknown game asset {value!r}: the game would reject the whole save",
            )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_ref(path: str, value: Any, type_name: str, catalog: Catalog) -> Iterator[Issue]:
    if value is None:
        return
    guid = ref_guid(value)
    if guid is None:
        yield Issue(ERROR, path, f"expected a reference to a {type_name}")
    elif guid in catalog and not catalog.is_a(guid, type_name):
        yield Issue(
            ERROR, path, f"{catalog.name(guid)} is a {catalog.type_of(guid)}, not a {type_name}"
        )


def _check_enum(path: str, value: Any, enum_name: str, catalog: Catalog) -> Iterator[Issue]:
    members = catalog.enum(enum_name)
    if not members:
        return
    if not isinstance(value, str) or "," not in value:
        yield Issue(ERROR, path, f"expected a {enum_name.split('/')[-1]} value")
        return
    name, _, number = value.partition(",")
    if name not in members and not number.lstrip("-").isdigit():
        yield Issue(ERROR, path, f"unknown {enum_name.split('/')[-1]} {name!r}")


def _check_types(tree: dict[str, Any], catalog: Catalog) -> Iterator[Issue]:
    for name, type_name in _REF_LISTS.items():
        items = tree.get(name)
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            yield from _check_ref(f"{name}[{i}]", item, type_name, catalog)
            guid = ref_guid(item)
            asset = catalog.get(guid)
            if asset and asset.get("usable") is False:
                yield Issue(
                    WARNING,
                    f"{name}[{i}]",
                    f"{catalog.pretty(guid)} isn't usable in game; it will be dropped",
                )

    pieces = tree.get("ownedFabricPieces")
    if isinstance(pieces, list):
        for i, piece in enumerate(pieces):
            if isinstance(piece, dict):
                yield from _check_ref(
                    f"ownedFabricPieces[{i}].fabric", piece.get("fabric"), "Fabric", catalog
                )

    for name, type_name in _REF_KEYED_DICTS.items():
        yield from _check_game_dict(
            tree, name, catalog, lambda p, k, t=type_name: _check_ref(p, k, t, catalog)
        )
    for name, enum_name in _ENUM_KEYED_DICTS.items():
        yield from _check_game_dict(
            tree, name, catalog, lambda p, k, e=enum_name: _check_enum(p, k, e, catalog)
        )

    for list_name in ("activeCommissions", "completedQuests"):
        states = tree.get(list_name)
        if not isinstance(states, list):
            continue
        for i, state in enumerate(states):
            path = f"{list_name}[{i}]"
            if state is None:
                continue
            if not isinstance(state, dict):
                yield Issue(ERROR, path, "expected a quest")
                continue
            if list_name == "completedQuests" and not state.get("questDefinition"):
                yield Issue(ERROR, f"{path}.questDefinition", "a completed quest needs a quest")
            for field, type_name in _QUEST_STATE_REFS.items():
                yield from _check_ref(f"{path}.{field}", state.get(field), type_name, catalog)
            if "outcome" in state:
                yield from _check_enum(f"{path}.outcome", state["outcome"], _OUTCOME_ENUM, catalog)
            for field in _QUEST_STATE_INTS:
                if field in state and not _is_int(state[field]):
                    yield Issue(ERROR, f"{path}.{field}", "must be a whole number")
            if list_name == "completedQuests" and state.get("dress") is None:
                yield Issue(
                    WARNING,
                    f"{path}.dress",
                    "completed quest has no dress; the game can crash when it shows this dress",
                )


def _check_game_dict(tree, name, catalog, check_key) -> Iterator[Issue]:
    value = tree.get(name)
    if value is None:
        return
    if not isinstance(value, dict) or not isinstance(value.get("@Keys", []), list):
        yield Issue(ERROR, name, "expected a dictionary")
        return
    keys = value.get("@Keys", [])
    values = value.get("@Values", [])
    if len(keys) != len(values):
        yield Issue(ERROR, name, "keys and values don't line up")
    for i, key in enumerate(keys):
        yield from check_key(f"{name}.@Keys[{i}]", key)
    if name in ("storyFlags",):
        for i, item in enumerate(values):
            if not isinstance(item, bool):
                yield Issue(ERROR, f"{name}.@Values[{i}]", "must be true or false")
    elif name in ("numericVariables", "characterRelationships", "ownedAccessories"):
        for i, item in enumerate(values):
            if not _is_number(item):
                yield Issue(ERROR, f"{name}.@Values[{i}]", "must be a number")
