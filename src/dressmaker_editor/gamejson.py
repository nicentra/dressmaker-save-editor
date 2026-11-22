"""Read and write the JSON dialect Dressmaker uses for its save files.

The game ships its own JSON code (``Framework.JSONObject`` and friends). It
writes standard JSON, but its reader is hand-written and much stricter than
real JSON parsers:

* A number without ``.`` is an ``int``. Writing ``100.0`` where the game
  expects an ``int`` (or ``1e5`` anywhere) makes the whole save fail to load.
* Strings are never unescaped, so ``"`` and ``\\`` inside a string break it.

This module parses saves into plain Python values (dicts keep their order)
and writes them back byte-for-byte the way the game formats them. Floats
that came from the file keep their original text, so values nobody touched
are written back unchanged even where float formatting is ambiguous.
"""

from __future__ import annotations

import json
import math
import re
import struct
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

__all__ = [
    "GameFloat",
    "dumps",
    "format_float",
    "is_valid_string",
    "loads",
    "to_float32",
]

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1


class GameFloat(float):
    """A float read from a save file, remembering the exact text it had.

    Arithmetic on it returns plain floats, which the writer formats the way
    the game does. Only values that were never changed keep their raw text.
    """

    raw: str

    def __new__(cls, raw: str) -> GameFloat:
        obj = super().__new__(cls, raw)
        obj.raw = raw
        return obj

    def __repr__(self) -> str:
        return f"GameFloat({self.raw!r})"

    def __copy__(self) -> GameFloat:
        return self

    def __deepcopy__(self, memo: dict) -> GameFloat:
        return self


def loads(text: str) -> Any:
    """Parse save-file text.

    Floats become :class:`GameFloat` so their original text survives a round
    trip. Strings are taken as the game sees them; the game never writes
    escape sequences, so none are expected.
    """
    if text.startswith("﻿"):
        text = text[1:]
    return json.loads(text, parse_float=GameFloat, parse_constant=_reject_constant)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid in a Dressmaker save")


def dumps(value: Any) -> str:
    """Serialize ``value`` exactly as the game's ``GetJSONString(true)`` would.

    The game builds compact JSON and then re-indents it with tabs, putting
    ``" : "`` between names and values and starting nested objects and arrays
    on a new line. This writes that format directly.
    """
    out: list[str] = []
    _write(value, 0, out)
    return "".join(out)


def _write(value: Any, level: int, out: list[str]) -> None:
    if isinstance(value, dict):
        inner = "\t" * (level + 1)
        out.append("{\n")
        out.append(inner)
        first = True
        for key, item in value.items():
            if not first:
                out.append(",\n")
                out.append(inner)
            first = False
            out.append('"')
            out.append(key)
            out.append('" : ')
            if isinstance(item, (dict, list)):
                out.append("\n")
                out.append(inner)
            _write(item, level + 1, out)
        out.append("\n")
        out.append("\t" * level)
        out.append("}")
    elif isinstance(value, list):
        inner = "\t" * (level + 1)
        out.append("[\n")
        out.append(inner)
        for i, item in enumerate(value):
            if i:
                out.append(",\n")
                out.append(inner)
            _write(item, level + 1, out)
        out.append("\n")
        out.append("\t" * level)
        out.append("]")
    else:
        out.append(_scalar(value))


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, GameFloat):
        return value.raw
    if isinstance(value, int):
        if not INT_MIN <= value <= INT_MAX:
            raise ValueError(f"{value} does not fit in the game's 32-bit int")
        return str(value)
    if isinstance(value, float):
        return format_float(value)
    if isinstance(value, str):
        return '"' + value + '"'
    raise TypeError(f"cannot write {type(value).__name__} to a save file")


def to_float32(value: float) -> float:
    """Round a Python float to the nearest 32-bit float, like a C# ``float``."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


_SEVEN_DIGITS = Context(prec=7, rounding=ROUND_HALF_UP)


def _fixed(value: float, decimals: int) -> str:
    """Format a float32 like Mono's ``float.ToString("F<decimals>")``.

    Mono formats ``float`` with seven significant digits and pads the rest
    with zeros, so ``0.1f.ToString("F10")`` is ``0.1000000000``.
    """
    rounded = _SEVEN_DIGITS.plus(Decimal(value))
    return f"{rounded:.{decimals}f}"


def format_float(value: float) -> str:
    """Format a float the way ``Framework.JSONFloatValue.Format`` does.

    The game writes the integer part and the fraction separately, so the
    result always contains a ``.`` and never an exponent.
    """
    value = to_float32(value)
    if math.isnan(value):
        return "0.0"
    sign = ""
    if value < 0:
        if math.isinf(value):
            return "-340282300000000000000000000000000000000.0"
        sign = "-"
        value = -value
    elif math.isinf(value):
        return "340282300000000000000000000000000000000.0"
    whole = to_float32(math.floor(value))
    fraction = to_float32(value - whole)
    if fraction == 0:
        return sign + _fixed(whole, 1)
    return sign + _fixed(whole, 0) + "." + _fixed(fraction, 29).rstrip("0")[2:]


_BAD_STRING = re.compile(r'["\\\x00-\x1f\x7f]')


def is_valid_string(value: str) -> bool:
    """Whether the game can read ``value`` back as a string.

    The game's reader toggles "inside a string" on every ``"`` and never
    unescapes anything, and its formatter strips nothing inside strings, so
    quotes, backslashes and control characters are all unsafe.
    """
    return not _BAD_STRING.search(value)
