from __future__ import annotations

from dressmaker_editor.gamejson import GameFloat
from dressmaker_editor.validate import ERROR, errors, validate


def test_real_saves_are_clean(fresh, midgame, catalog):
    assert errors(validate(fresh.tree, catalog)) == []
    assert errors(validate(midgame.tree, catalog)) == []


def test_unknown_guid_is_an_error(fresh, catalog):
    fresh.tree["unlockedFabrics"].append("0123456789abcdef0123456789abcdef,Imaginary Silk")
    issues = errors(validate(fresh.tree, catalog))
    assert any("unknown game asset" in i.message for i in issues)


def test_wrong_asset_type_is_an_error(fresh, catalog):
    character = catalog.characters()[0]
    fresh.tree["unlockedFabrics"].append(catalog.ref(character["guid"]))
    assert any("not a Fabric" in i.message for i in errors(validate(fresh.tree, catalog)))


def test_float_in_int_field_is_an_error(fresh, catalog):
    fresh.tree["gold"] = 100.0
    assert [i.path for i in errors(validate(fresh.tree, catalog))] == ["gold"]


def test_exponent_is_an_error(fresh, catalog):
    fresh.tree["prestige"] = GameFloat("1e5")
    assert any("exponent" in i.message for i in errors(validate(fresh.tree, catalog)))


def test_quote_in_name_is_an_error(fresh, catalog):
    fresh.tree["playerName"] = 'The "Best"'
    issues = errors(validate(fresh.tree, catalog))
    assert issues and issues[0].severity == ERROR


def test_unknown_story_flag_is_an_error(fresh, catalog):
    fresh.story_flags.set("NotARealFlag,x", True, lambda k: k == "NotARealFlag,x")
    assert any("StoryFlag" in i.message for i in errors(validate(fresh.tree, catalog)))


def test_without_catalog_only_syntax_is_checked(fresh):
    fresh.tree["unlockedFabrics"].append("0123456789abcdef0123456789abcdef,Imaginary Silk")
    assert errors(validate(fresh.tree, None)) == []
