"""Story consistency: progress that normal play could never produce.

None of this makes the game reject a save (that's :mod:`.validate`'s job), but
it can leave the story in an odd state: a side commission done before the
commission that unlocks it, a gap in the main story, the same commission
listed twice. Each problem comes with a fix.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from .catalog import Catalog
from .savefile import SaveData, ref_guid
from .story import Story


@dataclass
class StoryIssue:
    message: str
    fix_label: str
    fix: Callable[[], None]


def story_issues(save: SaveData, catalog: Catalog) -> list[StoryIssue]:
    story = Story(save, catalog)
    pretty = catalog.pretty
    issues: list[StoryIssue] = []

    # Gaps in the main story: something later is done, something earlier isn't.
    main = catalog.main_quests
    done = [story.is_completed(guid) for guid in main]
    last_done = max((i for i, d in enumerate(done) if d), default=-1)
    missing = [guid for i, guid in enumerate(main[: max(last_done, 0)]) if not done[i]]
    if missing:
        issues.append(
            StoryIssue(
                f"{_names(catalog, missing)} {_is_are(missing)} not done, but the later main "
                f"commission {pretty(main[last_done])} is.",
                "Mark the missing commissions done",
                lambda: [story.complete(guid) for guid in missing],
            )
        )

    final = catalog.final_quest
    if final and story.is_completed(final) and not all(done):
        rest = [guid for guid, d in zip(main, done, strict=True) if not d]
        issues.append(
            StoryIssue(
                f"The final commission is done, but {_names(catalog, rest)} {_is_are(rest)} not.",
                "Mark the remaining main commissions done",
                lambda: [story.complete(guid) for guid in rest],
            )
        )

    # The same commission completed more than once (only freeform dresses repeat).
    counts = Counter(
        guid
        for state in save.completed_quests
        if isinstance(state, dict)
        and (guid := ref_guid(state.get("questDefinition")))
        and guid != catalog.repeating_quest
    )
    for guid, count in counts.items():
        if count > 1:
            issues.append(
                StoryIssue(
                    f"{pretty(guid)} is listed as done {count} times.",
                    "Keep only the first",
                    lambda guid=guid: _keep_first(save, guid),
                )
            )

    # Both in progress and done.
    for guid in dict.fromkeys(
        ref_guid(s.get("questDefinition")) for s in save.active_commissions if isinstance(s, dict)
    ):
        if guid and guid != catalog.repeating_quest and story.is_completed(guid):
            issues.append(
                StoryIssue(
                    f"{pretty(guid)} is both in progress and done.",
                    "Remove the copy in progress",
                    lambda guid=guid: _drop_active(save, guid),
                )
            )

    # Side and gift commissions the story hasn't unlocked yet.
    for guid in story.locked_but_started():
        state = "done" if story.is_completed(guid) else "in progress"
        unlock = story.unlocked_by(guid)
        reason = (
            f"it only unlocks after {pretty(unlock)}, which isn't done yet"
            if unlock
            else "it isn't unlocked yet"
        )
        issues.append(
            StoryIssue(
                f"{pretty(guid)} is {state}, but {reason}.",
                f"Clear {pretty(guid)}",
                lambda guid=guid: story.reset(guid),
            )
        )
    return issues


def fix_all(save: SaveData, catalog: Catalog) -> int:
    """Apply fixes until nothing is left; returns how many were applied.

    Fixes are applied one at a time and the list is rebuilt after each,
    because one fix can resolve others (filling a main story gap can unlock
    a side commission that looked out of place).
    """
    applied = 0
    for _ in range(500):
        issues = story_issues(save, catalog)
        if not issues:
            break
        issues[0].fix()
        applied += 1
    return applied


def _keep_first(save: SaveData, guid: str) -> None:
    seen = False
    kept = []
    for state in save.completed_quests:
        if isinstance(state, dict) and ref_guid(state.get("questDefinition")) == guid:
            if seen:
                continue
            seen = True
        kept.append(state)
    save.completed_quests[:] = kept


def _drop_active(save: SaveData, guid: str) -> None:
    current = save.current_quest_state()
    active = [
        s
        for s in save.active_commissions
        if not (isinstance(s, dict) and ref_guid(s.get("questDefinition")) == guid)
    ]
    save.active_commissions[:] = active
    if current is not None and current in active:
        save.fix_active_commission_index(active.index(current))
    else:
        save.fix_active_commission_index(len(active) - 1)


def _names(catalog: Catalog, guids: list[str]) -> str:
    names = [catalog.pretty(g) for g in guids]
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    if len(names) > 1:
        return f"{', '.join(names[:-1])} and {names[-1]}"
    return names[0]


def _is_are(items: list[str]) -> str:
    return "are" if len(items) > 1 else "is"
