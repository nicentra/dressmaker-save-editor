"""The game's item and story data: every asset a save file can refer to.

Saves refer to game assets as ``"<guid>,<name>"`` and the game looks them up
by guid. An unknown guid makes the game reject the whole save, so the editor
checks every reference against this catalog before it writes anything.

The catalog is extracted from the installed game (see :mod:`.extract`) and
cached per game build. A snapshot ships with the editor as a fallback.
"""

from __future__ import annotations

import json
from functools import cached_property
from importlib import resources
from pathlib import Path
from typing import Any

SCHEMA = 1

GARMENT_TYPES = ("BodiceDefinition", "SkirtDefinition", "SleeveDefinition", "CollarDefinition")

# Names the game itself hard-codes (``QuestDefinition.RepeatingQuest`` etc.).
REPEATING_QUEST = "RepeatingQuest"
FINAL_QUEST = "FinalQuest"


class Catalog:
    """Read-only view over the extracted catalog JSON."""

    def __init__(self, data: dict[str, Any]):
        if data.get("schema") != SCHEMA:
            raise ValueError(f"unsupported catalog schema {data.get('schema')!r}")
        self.data = data
        self.assets: dict[str, dict[str, Any]] = data["assets"]
        self.bases: dict[str, list[str]] = data.get("bases", {})
        self.enums: dict[str, dict[str, int]] = data.get("enums", {})
        self.campaign: dict[str, Any] = data.get("campaign", {})
        self.source: dict[str, Any] = data.get("source", {})

    # ── loading ───────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path) -> Catalog:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def bundled(cls) -> Catalog:
        """The snapshot that ships with the editor."""
        text = (
            resources.files("dressmaker_editor.data")
            .joinpath("catalog.json")
            .read_text(encoding="utf-8")
        )
        catalog = cls(json.loads(text))
        catalog.source["bundled"] = True
        return catalog

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    @property
    def is_bundled(self) -> bool:
        return bool(self.source.get("bundled"))

    # ── assets ────────────────────────────────────────────────────────────
    def get(self, guid: str | None) -> dict[str, Any] | None:
        return self.assets.get(guid) if guid else None

    def __contains__(self, guid: object) -> bool:
        return guid in self.assets

    def type_of(self, guid: str) -> str | None:
        asset = self.assets.get(guid)
        return asset["type"] if asset else None

    def is_a(self, guid: str, type_name: str) -> bool:
        """Whether the asset is ``type_name`` or a subclass of it."""
        asset_type = self.type_of(guid)
        if asset_type is None:
            return False
        return asset_type == type_name or type_name in self.bases.get(asset_type, [])

    def of_type(self, *type_names: str) -> list[dict[str, Any]]:
        return [a for a in self.assets.values() if any(self.is_a(a["guid"], t) for t in type_names)]

    def name(self, guid: str) -> str:
        asset = self.assets.get(guid)
        return asset["name"] if asset else guid

    def pretty(self, guid: str | None) -> str:
        """A human-friendly name for an asset."""
        if not guid:
            return ""
        asset = self.assets.get(guid)
        if not asset:
            return guid
        return asset.get("pretty") or asset["name"]

    def ref(self, guid: str) -> str:
        """The ``"<guid>,<name>"`` string a save file uses for this asset."""
        return f"{guid},{self.assets[guid]['name']}"

    def find(self, type_name: str, name: str) -> dict[str, Any] | None:
        for asset in self.assets.values():
            if asset["name"] == name and self.is_a(asset["guid"], type_name):
                return asset
        return None

    # ── enums ─────────────────────────────────────────────────────────────
    def enum(self, name: str) -> dict[str, int]:
        return self.enums.get(name, {})

    def enum_ref(self, enum_name: str, member: str) -> str:
        """The ``"<member>,<value>"`` string the game writes for a C# enum."""
        return f"{member},{self.enum(enum_name)[member]}"

    @cached_property
    def outcome_values(self) -> dict[str, int]:
        return self.enum("QuestDefinition/QuestOutcome") or {
            "NotCompleted": 0,
            "Failed": 1,
            "Succeeded": 2,
            "Excelled": 3,
        }

    # ── story ─────────────────────────────────────────────────────────────
    def quest(self, guid: str) -> dict[str, Any] | None:
        asset = self.assets.get(guid)
        return asset if asset and asset["type"] == "QuestDefinition" else None

    def quest_named(self, name: str) -> dict[str, Any] | None:
        return self.find("QuestDefinition", name)

    @cached_property
    def main_quests(self) -> list[str]:
        """The main story, in the order the front desk hands it out."""
        return [g for g in self.campaign.get("main", []) if g in self.assets]

    @cached_property
    def side_quests(self) -> list[str]:
        """Side quests the messenger pigeon can bring."""
        return [g for g in self.campaign.get("side", []) if g in self.assets]

    @cached_property
    def gift_quests(self) -> dict[str, str]:
        """Gift quest guid → the character it belongs to."""
        out = {}
        for character in self.of_type("CharacterDefinition"):
            gift = character.get("giftQuest")
            if gift and gift in self.assets:
                out[gift] = character["guid"]
        return out

    @cached_property
    def final_quest(self) -> str | None:
        quest = self.quest_named(FINAL_QUEST)
        return quest["guid"] if quest else None

    @cached_property
    def repeating_quest(self) -> str | None:
        quest = self.quest_named(REPEATING_QUEST)
        return quest["guid"] if quest else None

    def unlock(self, name: str) -> dict[str, Any] | None:
        """One of the campaign's feature unlocks: gifting, nonCommission, pigeon."""
        return self.campaign.get("unlocks", {}).get(name)

    def characters(self) -> list[dict[str, Any]]:
        return sorted(self.of_type("CharacterDefinition"), key=lambda c: c["name"])
