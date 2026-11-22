from __future__ import annotations

import pytest

from dressmaker_editor import gamejson
from dressmaker_editor.savefile import SaveData
from dressmaker_editor.story import Status, Story, placeholder_dress
from dressmaker_editor.validate import errors, validate


def reload(save: SaveData) -> SaveData:
    """What the game would read back after the editor saves."""
    return SaveData.loads(save.dumps())


def test_fresh_game_is_at_the_start(fresh, catalog):
    story = Story(fresh, catalog)
    assert story.position() == 0
    rows = story.main_rows()
    assert rows[0].status is Status.AVAILABLE
    assert rows[1].status is Status.UPCOMING
    assert all(r.status is Status.LOCKED for r in story.side_rows())


def test_midgame_position(midgame, catalog):
    story = Story(midgame, catalog)
    assert story.position() == 16
    assert story.feature_unlocked("pigeon")
    statuses = {r.status for r in story.side_rows()}
    assert {Status.COMPLETED, Status.AVAILABLE, Status.LOCKED} <= statuses


def test_jump_ahead_completes_earlier_main_quests(fresh, catalog):
    story = Story(fresh, catalog)
    changes = story.jump_to(10)
    assert changes.completed == catalog.main_quests[:10]
    assert story.position() == 10
    saved = reload(fresh)
    assert Story(saved, catalog).position() == 10
    assert errors(validate(saved.tree, catalog)) == []
    for state in saved.completed_quests:
        assert state["outcome"] == "Succeeded,2"
        assert state["dress"]["finished"] is True
        assert state["dress"]["accessoryStates"] == []


def test_jump_unlocks_the_matching_side_quests(fresh, catalog):
    story = Story(fresh, catalog)
    story.jump_to(len(catalog.main_quests))
    rows = story.side_rows()
    assert all(r.status is Status.AVAILABLE for r in rows)


def test_jump_back_clears_later_main_quests(midgame, catalog):
    story = Story(midgame, catalog)
    side_before = [r.status for r in story.side_rows()]
    changes = story.jump_to(2)
    assert changes.removed == catalog.main_quests[2:16]
    assert story.position() == 2
    # side quests are left alone
    assert [r.status for r in story.side_rows() if r.status is Status.COMPLETED] == [
        s for s in side_before if s is Status.COMPLETED
    ]


def test_jump_to_finished_and_back(fresh, catalog):
    story = Story(fresh, catalog)
    story.jump_to(story.finished_position)
    assert story.position() == story.finished_position
    assert story.is_completed(catalog.final_quest)
    story.jump_to(len(catalog.main_quests))
    assert not story.is_completed(catalog.final_quest)
    assert story.position() == len(catalog.main_quests)


def test_jump_with_rewards_pays_budget_and_prestige(fresh, catalog):
    story = Story(fresh, catalog)
    gold = fresh.gold
    changes = story.jump_to(3, pay_rewards=True)
    budgets = sum(catalog.quest(g)["budget"] for g in catalog.main_quests[:3])
    assert fresh.gold == gold + budgets == gold + changes.gold
    # 6/6 + 6/7 + 6/8 prestige for three successful commissions
    assert fresh.prestige == pytest.approx(1 + 6 / 7 + 6 / 8, rel=1e-5)


def test_reset_side_quest_makes_it_replayable(midgame, catalog):
    story = Story(midgame, catalog)
    [done] = [r for r in story.side_rows() if r.status is Status.COMPLETED]
    count = len(midgame.completed_quests)
    assert story.reset(done.guid)
    assert len(midgame.completed_quests) == count - 1
    row = next(r for r in story.side_rows() if r.guid == done.guid)
    assert row.status is Status.AVAILABLE
    assert errors(validate(reload(midgame).tree, catalog)) == []


def test_complete_side_quest(midgame, catalog):
    story = Story(midgame, catalog)
    target = next(r for r in story.side_rows() if r.status is Status.AVAILABLE)
    story.complete(target.guid)
    assert story.is_completed(target.guid)
    assert errors(validate(reload(midgame).tree, catalog)) == []


def test_complete_gift_quest_sets_recipient(fresh, catalog):
    story = Story(fresh, catalog)
    gift = next(iter(catalog.gift_quests))
    story.complete(gift)
    state = story.completed_state(gift)
    assert state["dressRecipient"] == catalog.ref(catalog.gift_quests[gift])


def test_completing_an_active_quest_moves_it(midgame, catalog):
    story = Story(midgame, catalog)
    next_quest = catalog.main_quests[16]
    midgame.active_commissions.append(
        {"questDefinition": catalog.ref(next_quest), "outcome": "NotCompleted,0", "dress": None}
    )
    midgame.active_commission_index = 0
    story.complete(next_quest)
    assert midgame.active_commissions == []
    assert midgame.active_commission_index == -1
    assert story.is_completed(next_quest)


def test_placeholder_dress_is_plain(midgame, catalog):
    dress = placeholder_dress(midgame, catalog)
    cotton = catalog.ref(catalog.find("Fabric", "CottonWhite")["guid"])
    assert {p["fabric"] for p in dress["panelStates"]} == {cotton}
    assert dress["accessoryStates"] == []
    assert not any(p["cutoutActive"] for p in dress["panelStates"])


def test_placeholder_for_empty_save_uses_bundled_template(fresh, catalog):
    dress = placeholder_dress(fresh, catalog)
    assert dress["bodiceDefinition"] and dress["panelStates"]
    assert errors(validate({"dress": dress, **fresh.tree}, catalog)) == []


def test_gold_and_prestige_setters(fresh):
    fresh.gold = 5000
    fresh.prestige = 12.5
    assert reload(fresh).gold == 5000
    assert reload(fresh).prestige == 12.5
    assert fresh.prestige_level == 12
    with pytest.raises(ValueError):
        fresh.gold = -1
    with pytest.raises(ValueError):
        fresh.gold = gamejson.INT_MAX + 1


def test_unchanged_prestige_keeps_its_text(midgame):
    raw = midgame.tree["prestige"].raw
    midgame.prestige = midgame.prestige
    assert midgame.tree["prestige"].raw == raw
