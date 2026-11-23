from __future__ import annotations

import copy

from dressmaker_editor.checks import fix_all, story_issues
from dressmaker_editor.story import Status, Story
from dressmaker_editor.validate import errors, validate


def messages(save, catalog) -> list[str]:
    return [issue.message for issue in story_issues(save, catalog)]


def test_real_saves_have_no_story_issues(fresh, midgame, catalog):
    assert messages(fresh, catalog) == []
    assert messages(midgame, catalog) == []


def test_gap_in_main_story(midgame, catalog):
    story = Story(midgame, catalog)
    story.reset(catalog.main_quests[5])
    [issue] = story_issues(midgame, catalog)
    assert catalog.pretty(catalog.main_quests[5]) in issue.message
    issue.fix()
    assert story.position() == 16
    assert messages(midgame, catalog) == []


def test_side_commission_done_before_its_unlock(midgame, catalog):
    story = Story(midgame, catalog)
    [side] = [r.guid for r in story.side_rows() if r.status is Status.COMPLETED]
    story.jump_to(5)
    [issue] = story_issues(midgame, catalog)
    assert catalog.pretty(side) in issue.message and "only unlocks after" in issue.message
    assert fix_all(midgame, catalog) == 1
    assert not story.is_completed(side)
    assert errors(validate(midgame.tree, catalog)) == []


def test_jumping_back_can_clear_locked_side_commissions(midgame, catalog):
    story = Story(midgame, catalog)
    [side] = [r.guid for r in story.side_rows() if r.status is Status.COMPLETED]
    changes = story.jump_to(5, clear_locked=True)
    assert side in changes.removed
    assert messages(midgame, catalog) == []


def test_duplicate_completion(midgame, catalog):
    first = midgame.completed_quests[0]
    midgame.completed_quests.append(copy.deepcopy(first))
    [issue] = story_issues(midgame, catalog)
    assert "listed as done 2 times" in issue.message
    issue.fix()
    assert midgame.completed_quests[0] is first
    assert messages(midgame, catalog) == []


def test_in_progress_and_done(midgame, catalog):
    done = midgame.completed_quests[0]
    midgame.active_commissions.append(copy.deepcopy(done))
    midgame.active_commission_index = 0
    [issue] = story_issues(midgame, catalog)
    assert "both in progress and done" in issue.message
    issue.fix()
    assert midgame.active_commissions == []
    assert midgame.active_commission_index == -1


def test_final_done_too_early(fresh, catalog):
    story = Story(fresh, catalog)
    story.complete(catalog.final_quest)
    [issue] = story_issues(fresh, catalog)
    assert issue.message.startswith("The final commission is done")
    issue.fix()
    assert story.position() == story.finished_position


def test_fix_all_handles_several_problems(midgame, catalog):
    story = Story(midgame, catalog)
    story.reset(catalog.main_quests[3])
    midgame.completed_quests.append(copy.deepcopy(midgame.completed_quests[0]))
    later = next(  # a side commission that unlocks after the 16 done main ones
        g
        for g in catalog.side_quests
        if catalog.main_quests.index(Story(midgame, catalog).unlocked_by(g)) >= 16
    )
    # the gap, the duplicate, and each locked side commission marked done
    # (the client's earlier ones are marked done with it)
    marked = story.complete(later).completed
    assert len(story_issues(midgame, catalog)) == 2 + len(marked)
    fix_all(midgame, catalog)
    assert messages(midgame, catalog) == []
    assert errors(validate(midgame.tree, catalog)) == []
