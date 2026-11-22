from __future__ import annotations

import pytest

from conftest import FIXTURES
from dressmaker_editor import gamejson
from dressmaker_editor.gamejson import GameFloat, dumps, format_float, loads


def game_format(compact: str) -> str:
    """A line-by-line port of ``Framework.JSONObject.FormatJSONString``."""
    out: list[str] = []
    level = 0
    in_string = False
    for i, c in enumerate(compact):
        if c == '"':
            in_string = not in_string
        if in_string:
            out.append(c)
            continue
        if c in "[{":
            level += 1
            out.append(c + "\n" + "\t" * level)
        elif c == ",":
            out.append(c + "\n" + "\t" * level)
        elif c in "]}":
            level -= 1
            out.append("\n" + "\t" * level + c)
        elif c == ":":
            out.append(" : ")
            if i < len(compact) - 1 and compact[i + 1] in "{[":
                out.append("\n" + "\t" * level)
        else:
            out.append(c)
    return "".join(out)


def compact(value) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(f'"{k}":{compact(v)}' for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(compact(v) for v in value) + "]"
    return gamejson._scalar(value)


@pytest.mark.parametrize("name", ["fresh.json", "fresh.json.cache", "midgame.json"])
def test_round_trip_is_byte_exact(name):
    text = (FIXTURES / name).read_bytes().decode("utf-8")
    assert dumps(loads(text)) == text


@pytest.mark.parametrize("name", ["fresh.json", "midgame.json"])
def test_writer_matches_the_games_formatter(name):
    tree = loads((FIXTURES / name).read_bytes().decode("utf-8"))
    assert dumps(tree) == game_format(compact(tree))


def test_empty_containers_use_the_games_layout():
    assert dumps({"a": [], "b": {}}) == '{\n\t"a" : \n\t[\n\t\t\n\t],\n\t"b" : \n\t{\n\t\t\n\t}\n}'


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (57.01710892, "57.01710892"),
        (0.3280374, "0.3280374"),
        (-0.0009593149, "-0.0009593149"),
        (11.0, "11.0"),
        (0.0, "0.0"),
        (3.5219371, "3.5219371"),
        (99.7457809, "99.7457809"),
        (1.0, "1.0"),
        (-2.0, "-2.0"),
        (0.000571798, "0.000571798"),
        (123456789.0, "123456800.0"),
    ],
)
def test_float_format_matches_the_game(value, text):
    assert format_float(value) == text


def test_floats_keep_their_text_and_ints_stay_ints():
    tree = loads('{"a" : 1.50000000, "b" : 2, "c" : -1}')
    assert isinstance(tree["a"], GameFloat)
    assert tree["a"].raw == "1.50000000"
    assert isinstance(tree["b"], int)
    assert dumps(tree) == '{\n\t"a" : 1.50000000,\n\t"b" : 2,\n\t"c" : -1\n}'


def test_changed_floats_are_formatted_like_the_game():
    tree = loads('{"a" : 1.5}')
    tree["a"] = tree["a"] + 1
    assert dumps(tree) == '{\n\t"a" : 2.5\n}'


def test_ints_must_fit_in_32_bits():
    with pytest.raises(ValueError):
        dumps({"gold": 2**31})


@pytest.mark.parametrize("text", ['say "hi"', "back\\slash", "new\nline", "tab\t"])
def test_unsafe_strings(text):
    assert not gamejson.is_valid_string(text)


def test_safe_strings():
    assert gamejson.is_valid_string("Élise's Atelier, est. 1912")
