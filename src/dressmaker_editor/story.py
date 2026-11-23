"""Story progress: where the player is in the main story, and side quests.

How the game decides what comes next (from its ``FrontDesk`` and
``MessengerPigeon`` code):

* The main story is the campaign's ordered quest list. The front desk always
  offers the first one that is neither completed nor in progress. When all of
  them are done, the knighthood letter starts the final quest.
* Side quests come by messenger pigeon once their requirements are met,
  usually "finished main quest X", and only while the same client has no
  other commission open. The pigeon offers each client only the first of
  their side quests (in the campaign's list) that is unlocked and not done, so
  a client's side quests come one after another. The editor keeps that order:
  replaying one also clears the client's later ones, and marking one done
  also completes the earlier ones.
* A quest counts as done when ``completedQuests`` holds an entry for it, so
  completing a quest means adding an entry and replaying one means removing it.

Completed quests need a dress: the sketchbook's dress history and the ending
credits load it and crash without one. Quests completed by the editor get a
plain placeholder dress.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from enum import Enum
from importlib import resources
from typing import Any

from . import gamejson
from .catalog import Catalog
from .savefile import SaveData, by_guid, ref_guid

OUTCOME_ENUM = "QuestDefinition/QuestOutcome"
SUCCEEDED = "Succeeded"
PLACEHOLDER_FABRIC = "CottonWhite"


class Status(Enum):
    COMPLETED = "Completed"
    ACTIVE = "In progress"
    AVAILABLE = "Available"
    LOCKED = "Locked"
    UPCOMING = "Not reached yet"


@dataclass
class QuestRow:
    guid: str
    kind: str  # "main", "side", "gift" or "final"
    title: str
    client: str
    status: Status
    outcome: str | None = None
    unlocked_by: str | None = None  # guid of the quest that unlocks it
    index: int | None = None  # position in the main story


@dataclass
class Changes:
    completed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    gold: int = 0
    prestige: float = 0.0
    friendship: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.completed or self.removed)


class Story:
    def __init__(self, save: SaveData, catalog: Catalog):
        self.save = save
        self.catalog = catalog

    # ── lookups ───────────────────────────────────────────────────────────
    def _state_quest(self, state: Any) -> str | None:
        return ref_guid(state.get("questDefinition")) if isinstance(state, dict) else None

    def completed_state(self, guid: str) -> dict[str, Any] | None:
        """``PlayerProgress.GetQuestState``: the first completed entry for a quest."""
        for state in self.save.completed_quests:
            if self._state_quest(state) == guid:
                return state
        return None

    def is_completed(self, guid: str) -> bool:
        return self.completed_state(guid) is not None

    def is_active(self, guid: str) -> bool:
        return any(self._state_quest(s) == guid for s in self.save.active_commissions)

    def outcome_value(self, state: dict[str, Any]) -> int:
        raw = state.get("outcome")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            name, _, number = raw.partition(",")
            values = self.catalog.outcome_values
            if name in values:
                return values[name]
            if number.lstrip("-").isdigit():
                return int(number)
        return 0

    def outcome_name(self, state: dict[str, Any]) -> str:
        value = self.outcome_value(state)
        for name, number in self.catalog.outcome_values.items():
            if number == value:
                return name
        return str(value)

    # ── requirements (StoryletRequirement.IsMet) ──────────────────────────
    def requirement_met(self, req: dict[str, Any] | None) -> bool | None:
        """Evaluate one storylet requirement; None if its type is unknown."""
        if not req:
            return True
        kind = req.get("type")
        if kind == "QuestOutcomeRequirement":
            quest = req.get("quest")
            if not quest:
                return False
            state = self.completed_state(quest)
            outcome = req.get("outcome", 2)
            if outcome == 0:
                return state is None
            if state is None:
                return False
            return _compare(self.outcome_value(state), req.get("comparison", 0), outcome)
        if kind == "StoryFlagRequirement":
            flag = _member(self.catalog.enum("StoryFlag"), req.get("flag"))
            return self.save.story_flag(flag) == bool(req.get("requiredValue", True))
        if kind == "StoryVariableRequirement":
            name = _member(self.catalog.enum("StoryVariable"), req.get("variable"))
            return _compare(
                self.save.numeric_variable(name), req.get("comparison", 1), req.get("value", 0)
            )
        if kind == "CharacterRelationshipRequirement":
            character = req.get("character")
            if not character:
                return False
            return _compare(
                self.save.relationship(character), req.get("comparison", 0), req.get("value", 0)
            )
        if kind == "PrestigeRequirement":
            return _compare(self.save.prestige, req.get("comparison", 0), req.get("value", 0))
        if kind == "ActiveQuestRequirement":
            return bool(req.get("quest")) and self.is_active(req["quest"])
        if kind == "ActiveQuestCountRequirement":
            return _compare(
                len(self.save.active_commissions), req.get("comparison", 0), req.get("value", 0)
            )
        if kind == "HasSeenStoryletRequirement":
            report = req.get("gossipReport")
            seen = any(ref_guid(r) == report for r in self.save.shown_gossip_reports)
            return seen != bool(req.get("invert", False))
        if kind == "ItemUnlockedRequirement":
            lists = {0: "unlockedFabrics", 1: "unlockedPatterns", 2: "unlockedAccessories"}
            fields = {0: "fabric", 1: "pattern", 2: "accessory"}
            item_type = req.get("itemType", 0)
            item = req.get(fields.get(item_type, ""))
            items = self.save.tree.get(lists.get(item_type, ""), [])
            return bool(item) and any(ref_guid(i) == item for i in items)
        return None

    def requirements_met(self, guid: str) -> bool:
        quest = self.catalog.quest(guid) or {}
        return all(self.requirement_met(r) is not False for r in quest.get("requirements", []))

    def feature_unlocked(self, name: str) -> bool:
        """Campaign feature unlocks: ``gifting``, ``nonCommission``, ``pigeon``."""
        return self.requirement_met(self.catalog.unlock(name)) is not False

    # ── the main story ────────────────────────────────────────────────────
    @property
    def main_quests(self) -> list[str]:
        return self.catalog.main_quests

    @property
    def finished_position(self) -> int:
        """The position meaning "the final quest is done too"."""
        return len(self.main_quests) + 1

    def position(self) -> int:
        """How far the main story has got.

        ``n`` means the first ``n`` main quests are done and quest ``n`` is next.
        ``len(main)`` means every main quest is done and the final quest is next;
        :attr:`finished_position` means the final quest is done as well.
        """
        for i, guid in enumerate(self.main_quests):
            if not self.is_completed(guid):
                return i
        final = self.catalog.final_quest
        if final and self.is_completed(final):
            return self.finished_position
        return len(self.main_quests)

    def position_label(self, position: int | None = None) -> str:
        position = self.position() if position is None else position
        total = len(self.main_quests)
        if position >= self.finished_position:
            return "Story finished (final commission completed)"
        if position >= total:
            return "All main commissions done: the final commission is next"
        guid = self.main_quests[position]
        return f"Commission {position + 1} of {total}: {self.catalog.pretty(guid)}"

    def _row(self, guid: str, kind: str, **extra: Any) -> QuestRow:
        quest = self.catalog.quest(guid) or {}
        state = self.completed_state(guid)
        if state is not None:
            status, outcome = Status.COMPLETED, self.outcome_name(state)
        elif self.is_active(guid):
            status, outcome = Status.ACTIVE, None
        else:
            status, outcome = Status.LOCKED, None
        giver = quest.get("giver")
        # The final and freeform commissions belong to a placeholder character.
        placeholder = (self.catalog.get(giver) or {}).get("name") == "Blank"
        return QuestRow(
            guid=guid,
            kind=kind,
            title=self.catalog.pretty(guid),
            client="" if placeholder else self.catalog.pretty(giver),
            status=status,
            outcome=outcome,
            **extra,
        )

    def main_rows(self) -> list[QuestRow]:
        rows = []
        next_index = self.position()
        for i, guid in enumerate(self.main_quests):
            row = self._row(guid, "main", index=i)
            if row.status is Status.LOCKED:
                row.status = Status.AVAILABLE if i == next_index else Status.UPCOMING
            rows.append(row)
        final = self.catalog.final_quest
        if final:
            row = self._row(final, "final", index=len(self.main_quests))
            if row.status is Status.LOCKED:
                done = all(self.is_completed(g) for g in self.main_quests)
                row.status = Status.AVAILABLE if done else Status.UPCOMING
            rows.append(row)
        return rows

    def is_unlocked(self, guid: str) -> bool:
        """Whether the game could offer this side or gift quest right now.

        Side quests need the messenger pigeon and their own requirements; gift
        quests need gifting. Main quests count as unlocked.
        """
        if guid in self.catalog.side_quests:
            feature: str | None = "pigeon"
        elif guid in self.catalog.gift_quests:
            feature = "gifting"
        else:
            return True
        return self.feature_unlocked(feature) and self.requirements_met(guid)

    def side_rows(self) -> list[QuestRow]:
        rows = []
        for guid in self.side_quests():
            row = self._row(guid, "side", unlocked_by=self._unlocking_quest(guid))
            if row.status is Status.LOCKED and self.is_unlocked(guid):
                row.status = Status.AVAILABLE
            rows.append(row)
        return rows

    def gift_rows(self) -> list[QuestRow]:
        unlock = self.catalog.unlock("gifting") or {}
        rows = []
        for guid in self.catalog.gift_quests:
            row = self._row(guid, "gift", unlocked_by=unlock.get("quest"))
            if row.status is Status.LOCKED and self.is_unlocked(guid):
                row.status = Status.AVAILABLE
            rows.append(row)
        rows.sort(key=lambda r: r.client)
        return rows

    def unlocked_by(self, guid: str) -> str | None:
        """The main quest that has to be done before this side or gift quest."""
        if guid in self.catalog.gift_quests:
            return (self.catalog.unlock("gifting") or {}).get("quest")
        return self._unlocking_quest(guid)

    def side_quests(self) -> list[str]:
        """Side quests grouped by client, each client's in the order they come."""
        ordered: list[str] = []
        for guid in self.catalog.side_quests:
            if guid not in ordered:
                ordered.extend(self.client_side_quests(guid))
        return ordered

    def client_side_quests(self, guid: str) -> list[str]:
        """The side quests of ``guid``'s client, in the order the pigeon brings them.

        The pigeon offers the first one in the campaign's list that is unlocked
        and not done yet. Played as they unlock, a client's side quests come in
        the order they become available (after the main quest that unlocks
        them, but not before the pigeon), and ones that become available
        together come in list order. Empty if ``guid`` isn't a side quest.
        """
        if guid not in self.catalog.side_quests:
            return []
        giver = (self.catalog.quest(guid) or {}).get("giver")
        if not giver:
            return [guid]
        main = {g: i for i, g in enumerate(self.main_quests)}
        pigeon = main.get((self.catalog.unlock("pigeon") or {}).get("quest"), -1)
        quests = [
            g
            for g in self.catalog.side_quests
            if (self.catalog.quest(g) or {}).get("giver") == giver
        ]
        # sorted() is stable, so quests available together keep their list order.
        return sorted(
            quests, key=lambda g: max(main.get(self._unlocking_quest(g), len(main)), pigeon)
        )

    def _before_and_after(self, guid: str) -> tuple[list[str], list[str]]:
        chain = self.client_side_quests(guid)
        if guid not in chain:
            return [], []
        i = chain.index(guid)
        return chain[:i], chain[i + 1 :]

    def replay_targets(self, guid: str) -> list[str]:
        """What :meth:`replay` clears: the quest and, for a side quest, the
        client's later side quests that are done or in progress."""
        _before, after = self._before_and_after(guid)
        return [guid, *(g for g in after if self.is_completed(g) or self.is_active(g))]

    def complete_targets(self, guid: str) -> list[str]:
        """What :meth:`complete` marks done: the quest and, for a side quest,
        the client's earlier side quests that aren't done yet."""
        before, _after = self._before_and_after(guid)
        return [*(g for g in before if not self.is_completed(g)), guid]

    def _unlocking_quest(self, guid: str) -> str | None:
        quest = self.catalog.quest(guid) or {}
        for req in quest.get("requirements", []):
            if req.get("type") == "QuestOutcomeRequirement" and req.get("quest"):
                return req["quest"]
        return None

    # ── changing progress ─────────────────────────────────────────────────
    def jump_to(
        self, position: int, *, pay_rewards: bool = False, clear_locked: bool = False
    ) -> Changes:
        """Make the story continue from ``position`` (see :meth:`position`).

        Main quests before it are completed (with placeholder dresses unless
        they already were), the ones from it onwards are cleared so the game
        hands them out again. Side and gift quests are left alone unless
        ``clear_locked`` is set; then the ones that are no longer unlocked
        (after jumping backwards) are cleared too.
        """
        position = max(0, min(position, self.finished_position))
        changes = Changes()
        main = self.main_quests
        final = self.catalog.final_quest
        keep_done = main[: min(position, len(main))]
        clear = main[len(keep_done) :]
        if final and position < self.finished_position:
            clear = [*clear, final]
        for guid in clear:
            if self.reset(guid):
                changes.removed.append(guid)
        targets = list(keep_done)
        if final and position >= self.finished_position:
            targets.append(final)
        for guid in targets:
            if not self.is_completed(guid):
                self._complete(guid, SUCCEEDED, pay_rewards, changes)
        if clear_locked:
            changes.removed.extend(self.clear_locked())
        return changes

    def locked_but_started(self) -> list[str]:
        """Side and gift quests that are done or in progress but not unlocked."""
        return [
            guid
            for guid in (*self.catalog.side_quests, *self.catalog.gift_quests)
            if (self.is_completed(guid) or self.is_active(guid)) and not self.is_unlocked(guid)
        ]

    def clear_locked(self) -> list[str]:
        """Clear side and gift quests the story hasn't unlocked yet."""
        cleared = []
        for guid in self.locked_but_started():
            if self.reset(guid):
                cleared.append(guid)
        return cleared

    def complete(
        self, guid: str, outcome: str = SUCCEEDED, *, pay_rewards: bool = False
    ) -> Changes:
        """Mark a quest as done, as if the player had delivered the dress.

        For a side quest, the client's earlier side quests are marked done
        (successfully) first, since the pigeon only brings them in order.
        """
        changes = Changes()
        for target in self.complete_targets(guid):
            if not self.is_completed(target):
                result = outcome if target == guid else SUCCEEDED
                self._complete(target, result, pay_rewards, changes)
        return changes

    def replay(self, guid: str) -> list[str]:
        """Clear a quest so it can be played again (see :meth:`reset`).

        For a side quest, the client's later side quests are cleared too, so
        the pigeon brings them again in order. Returns the quests it cleared.
        """
        return [target for target in self.replay_targets(guid) if self.reset(target)]

    def reset(self, guid: str) -> bool:
        """Forget a quest entirely, so the game offers it again when its turn comes.

        Removes every completed entry for it (and its dress) and abandons it if
        it is in progress. Rewards already paid out are kept.
        """
        save = self.save
        before = len(save.completed_quests) + len(save.active_commissions)
        save.completed_quests[:] = [
            s for s in save.completed_quests if self._state_quest(s) != guid
        ]
        current = save.current_quest_state()
        active = [s for s in save.active_commissions if self._state_quest(s) != guid]
        save.active_commissions[:] = active
        if current is not None and current in active:
            save.fix_active_commission_index(active.index(current))
        else:
            save.fix_active_commission_index(len(active) - 1)
        return len(save.completed_quests) + len(save.active_commissions) < before

    def _complete(self, guid: str, outcome: str, pay_rewards: bool, changes: Changes) -> None:
        save = self.save
        in_progress = next(
            (s for s in save.active_commissions if self._state_quest(s) == guid), None
        )
        if in_progress is not None:
            self.reset(guid)
        dress = None
        if in_progress is not None:
            dress = in_progress.get("dress")
            if not (isinstance(dress, dict) and dress.get("finished")):
                dress = None
        if dress is None:
            dress = placeholder_dress(save, self.catalog)
        state = self._new_state(guid, outcome, dress, in_progress)
        if pay_rewards:
            self._pay_rewards(guid, outcome, changes)
        save.completed_quests.append(state)
        changes.completed.append(guid)

    def _new_state(
        self, guid: str, outcome: str, dress: dict[str, Any], previous: dict[str, Any] | None
    ) -> dict[str, Any]:
        catalog = self.catalog
        recipient = None
        if guid in catalog.gift_quests:
            recipient = catalog.ref(catalog.gift_quests[guid])
        state: dict[str, Any] = {
            "questDefinition": catalog.ref(guid),
            "outcome": f"{outcome},{catalog.outcome_values[outcome]}",
            "dress": dress,
            "sketchbookPlacements": [],
            "sketchbookColorFills": [],
            "dressRecipient": recipient,
            "thumbnailGuid": None,
            "previewBodice": dress.get("bodiceDefinition"),
            "previewCollar": dress.get("collarDefinition"),
            "previewSleeve": dress.get("sleeveDefinition"),
            "previewSkirt": dress.get("skirtDefinition"),
            "previewBodiceVariant": _int(dress.get("bodiceVariant")),
            "previewCollarVariant": _int(dress.get("collarVariant")),
            "previewSleeveVariant": _int(dress.get("sleeveVariant")),
            "previewSkirtVariant": _int(dress.get("skirtVariant")),
        }
        if previous is not None:
            # Keep the player's own sketch and photo for a quest they had started.
            for key in ("sketchbookPlacements", "sketchbookColorFills", "thumbnailGuid"):
                if key in previous:
                    state[key] = previous[key]
            if previous.get("dressRecipient"):
                state["dressRecipient"] = previous["dressRecipient"]
        return state

    def _pay_rewards(self, guid: str, outcome: str, changes: Changes) -> None:
        """What ``FrontDesk.CompleteQuest`` pays when a commission is handed in.

        Success: +1 friendship with the client and, except for gifts, the
        budget in gold and ``6 / (6 + dresses so far)`` prestige. Failure: the
        budget (except for gifts), +0.25 friendship and a quarter of the prestige.
        """
        if guid == self.catalog.repeating_quest:
            return  # freeform dresses are sold in the photo studio instead
        save, catalog = self.save, self.catalog
        quest = catalog.quest(guid) or {}
        budget = int(quest.get("budget") or 0)
        gift = guid in catalog.gift_quests
        succeeded = catalog.outcome_values.get(outcome, 2) >= catalog.outcome_values[SUCCEEDED]
        dresses = sum(
            1
            for s in save.completed_quests
            if self._state_quest(s) and self._state_quest(s) not in catalog.gift_quests
        )
        scale = 6.0 / (6 + dresses)
        prestige = 0.0
        if succeeded:
            friendship = 1.0
            if not gift:
                prestige = 1.0 * scale
        else:
            friendship = 0.25
            prestige = 0.25 * scale  # yes, even for gifts: the game does the same
        if not gift:
            save.gold = min(gamejson.INT_MAX, save.gold + budget)
            changes.gold += budget
        if prestige:
            delta = gamejson.to_float32(prestige)
            save.prestige = gamejson.to_float32(save.prestige + delta)
            changes.prestige += delta
        giver = quest.get("giver")
        if giver and (catalog.get(giver) or {}).get("name") != "Blank":
            value = gamejson.to_float32(save.relationship(giver) + friendship)
            save.relationships.set(catalog.ref(giver), value, by_guid(giver))
            changes.friendship += friendship


def placeholder_dress(save: SaveData, catalog: Catalog) -> dict[str, Any]:
    """A plain dress for quests the player didn't actually sew.

    Built from the newest finished dress in the save (so it matches the
    installed game version) or, for a save without any, from the template
    that ships with the editor. Every panel is plain white cotton and
    accessories are removed, so it is obvious in game which dresses these are.
    """
    source = None
    for state in reversed([*save.completed_quests, *save.active_commissions]):
        dress = state.get("dress") if isinstance(state, dict) else None
        if isinstance(dress, dict) and dress.get("finished") and dress.get("bodiceDefinition"):
            source = dress
            break
    dress = copy.deepcopy(source) if source is not None else bundled_template_dress()
    fabric = catalog.find("Fabric", PLACEHOLDER_FABRIC)
    for panel in dress.get("panelStates") or []:
        if isinstance(panel, dict):
            if fabric:
                panel["fabric"] = catalog.ref(fabric["guid"])
            panel["cutoutActive"] = False
    dress["accessoryStates"] = []
    return dress


def bundled_template_dress() -> dict[str, Any]:
    text = (
        resources.files("dressmaker_editor.data")
        .joinpath("placeholder_dress.json")
        .read_text(encoding="utf-8")
    )
    return gamejson.loads(text)


def _compare(actual: float, comparison: int, target: float) -> bool:
    if comparison == 0:
        return actual >= target
    if comparison == 1:
        return math.isclose(actual, target, rel_tol=1e-6, abs_tol=1e-6)
    if comparison == 2:
        return actual <= target
    return False


def _member(members: dict[str, int], value: Any) -> str:
    for name, number in members.items():
        if number == value:
            return name
    return str(value)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
