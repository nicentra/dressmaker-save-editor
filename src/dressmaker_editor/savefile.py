"""A loaded save file, with typed access to the parts the editor changes.

Everything else in the file is kept exactly as it was, including fields this
editor doesn't know about, so saves from newer game versions survive.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from . import gamejson
from .gamejson import GameFloat

MAX_PRESTIGE_LEVEL = 50
MAX_RELATIONSHIP_LEVEL = 5
DEFAULT_PLAYER_NAME = "Dressmaker"

_REF = re.compile(r"^([0-9a-f]{32}),(.*)$", re.DOTALL)


def ref_guid(value: Any) -> str | None:
    """The guid in a ``"<guid>,<name>"`` asset reference, or None."""
    if isinstance(value, str):
        match = _REF.match(value)
        if match:
            return match.group(1)
    return None


def is_ref(value: Any) -> bool:
    return ref_guid(value) is not None


def enum_member(value: Any) -> str | None:
    """The member name in a ``"<Name>,<int>"`` enum value."""
    if isinstance(value, str) and "," in value:
        return value.split(",", 1)[0]
    return None


class GameDict:
    """A C# ``Dictionary`` as the game writes it: parallel key and value arrays."""

    def __init__(self, obj: dict[str, Any]):
        obj.setdefault("@Keys", [])
        obj.setdefault("@Values", [])
        self.obj = obj

    @property
    def keys(self) -> list[Any]:
        return self.obj["@Keys"]

    @property
    def values(self) -> list[Any]:
        return self.obj["@Values"]

    def items(self) -> Iterator[tuple[Any, Any]]:
        return zip(self.keys, self.values, strict=False)

    def __len__(self) -> int:
        return len(self.keys)

    def index(self, match: Callable[[Any], bool]) -> int | None:
        for i, key in enumerate(self.keys):
            if match(key):
                return i
        return None

    def get(self, match: Callable[[Any], bool], default: Any = None) -> Any:
        i = self.index(match)
        return self.values[i] if i is not None else default

    def set(self, key: Any, value: Any, match: Callable[[Any], bool]) -> None:
        i = self.index(match)
        if i is None:
            self.keys.append(key)
            self.values.append(value)
        else:
            self.keys[i] = key
            self.values[i] = value

    def remove(self, match: Callable[[Any], bool]) -> bool:
        i = self.index(match)
        if i is None:
            return False
        del self.keys[i]
        del self.values[i]
        return True


def by_guid(guid: str) -> Callable[[Any], bool]:
    return lambda key: ref_guid(key) == guid


def by_member(name: str) -> Callable[[Any], bool]:
    return lambda key: enum_member(key) == name


class SaveData:
    """The parsed contents of one ``player*.json`` file."""

    def __init__(self, tree: dict[str, Any]):
        if not isinstance(tree, dict):
            raise ValueError("a save file must be a JSON object")
        self.tree = tree

    @classmethod
    def loads(cls, text: str) -> SaveData:
        return cls(gamejson.loads(text))

    @classmethod
    def load(cls, path: Path) -> SaveData:
        return cls.loads(path.read_bytes().decode("utf-8"))

    def dumps(self) -> str:
        return gamejson.dumps(self.tree)

    def copy(self) -> SaveData:
        return SaveData(copy.deepcopy(self.tree))

    # ── scalars ───────────────────────────────────────────────────────────
    @property
    def player_name(self) -> str:
        name = self.tree.get("playerName")
        return name if isinstance(name, str) else DEFAULT_PLAYER_NAME

    @player_name.setter
    def player_name(self, value: str) -> None:
        if not gamejson.is_valid_string(value):
            raise ValueError("the name can't contain quotes, backslashes or control characters")
        self.tree["playerName"] = value

    @property
    def gold(self) -> int:
        gold = self.tree.get("gold", 0)
        return int(gold) if isinstance(gold, (int, float)) else 0

    @gold.setter
    def gold(self, value: int) -> None:
        value = int(value)
        if not 0 <= value <= gamejson.INT_MAX:
            raise ValueError(f"gold must be between 0 and {gamejson.INT_MAX:,}")
        self.tree["gold"] = value

    @property
    def prestige(self) -> float:
        prestige = self.tree.get("prestige", 0.0)
        return float(prestige) if isinstance(prestige, (int, float)) else 0.0

    @prestige.setter
    def prestige(self, value: float) -> None:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("prestige must be a positive number")
        current = self.tree.get("prestige")
        if isinstance(current, GameFloat) and gamejson.to_float32(
            float(current)
        ) == gamejson.to_float32(value):
            return  # unchanged: keep the original text
        self.tree["prestige"] = gamejson.to_float32(value)

    @property
    def prestige_level(self) -> int:
        """The rank shown in game: ``floor(prestige)``, capped at 50."""
        return max(0, min(MAX_PRESTIGE_LEVEL, math.floor(self.prestige)))

    # ── collections ───────────────────────────────────────────────────────
    def _list(self, name: str) -> list[Any]:
        value = self.tree.get(name)
        if not isinstance(value, list):
            value = []
            self.tree[name] = value
        return value

    def _dict(self, name: str) -> GameDict:
        value = self.tree.get(name)
        if not isinstance(value, dict):
            value = {}
            self.tree[name] = value
        return GameDict(value)

    @property
    def completed_quests(self) -> list[dict[str, Any]]:
        return self._list("completedQuests")

    @property
    def active_commissions(self) -> list[dict[str, Any]]:
        return self._list("activeCommissions")

    @property
    def active_commission_index(self) -> int:
        index = self.tree.get("activeCommissionIndex", -1)
        return index if isinstance(index, int) else -1

    @active_commission_index.setter
    def active_commission_index(self, value: int) -> None:
        self.tree["activeCommissionIndex"] = int(value)

    def fix_active_commission_index(self, prefer: int | None = None) -> None:
        """Clamp the index like ``PlayerProgress.ValidateAndRepair`` does."""
        count = len(self.active_commissions)
        if count == 0:
            self.active_commission_index = -1
            return
        index = self.active_commission_index if prefer is None else prefer
        self.active_commission_index = max(0, min(index, count - 1))

    @property
    def story_flags(self) -> GameDict:
        return self._dict("storyFlags")

    @property
    def numeric_variables(self) -> GameDict:
        return self._dict("numericVariables")

    @property
    def relationships(self) -> GameDict:
        return self._dict("characterRelationships")

    @property
    def shown_gossip_reports(self) -> list[str]:
        return self._list("shownGossipReports")

    def quest_states(self) -> Iterator[dict[str, Any]]:
        """Active commissions first, then completed quests (``AllQuestStates``)."""
        for state in self.active_commissions:
            if isinstance(state, dict):
                yield state
        for state in self.completed_quests:
            if isinstance(state, dict):
                yield state

    def thumbnail_guids(self) -> list[str]:
        return [
            s["thumbnailGuid"]
            for s in self.quest_states()
            if isinstance(s.get("thumbnailGuid"), str) and s["thumbnailGuid"]
        ]

    # ── story flags ───────────────────────────────────────────────────────
    def story_flag(self, name: str) -> bool:
        return self.story_flags.get(by_member(name), False) is True

    def numeric_variable(self, name: str) -> float:
        value = self.numeric_variables.get(by_member(name), 0.0)
        return float(value) if isinstance(value, (int, float)) else 0.0

    def relationship(self, character_guid: str) -> float:
        value = self.relationships.get(by_guid(character_guid), 0.0)
        return float(value) if isinstance(value, (int, float)) else 0.0

    def current_quest_state(self) -> dict[str, Any] | None:
        index = self.active_commission_index
        if 0 <= index < len(self.active_commissions):
            return self.active_commissions[index]
        return None
