"""Dimension-type registry and literal coercion (DESIGN.md §5).

Every literal in a contract is coerced to the declared type of the dimension it is compared
with. Coercion is strict: a value that does not unambiguously mean a value of that type is an
InvalidLiteral, never a guess.
"""
import datetime as dt
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from walt_compiler.errors import InvalidLiteral

_INTEGER = re.compile(r"-?\d+")
_NUMBER = re.compile(r"-?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?")


class _Reject(Exception):
    """Internal: the raw value is not a valid literal of the type."""


@dataclass(frozen=True)
class TypedLiteral:
    value: Any
    type: str


@dataclass(frozen=True)
class TypeSpec:
    name: str
    orderable: bool
    coerce: Callable[[Any], Any]


def _is_number(raw) -> bool:
    return isinstance(raw, (int, float)) and not isinstance(raw, bool)


def _text(raw):
    if isinstance(raw, str):
        return raw
    raise _Reject


def _integer(raw):
    if _is_number(raw):
        if isinstance(raw, int):
            return raw
        if math.isfinite(raw) and raw.is_integer():
            return int(raw)
    elif isinstance(raw, str) and _INTEGER.fullmatch(raw):
        return int(raw)
    raise _Reject


def _double(raw):
    if _is_number(raw) or (isinstance(raw, str) and _NUMBER.fullmatch(raw)):
        value = float(raw)
        if math.isfinite(value):
            return value
    raise _Reject


def _decimal(raw):
    if _is_number(raw) or (isinstance(raw, str) and _NUMBER.fullmatch(raw)):
        try:
            value = Decimal(str(raw))  # str() keeps 0.1 as 0.1, not its binary expansion
        except InvalidOperation:
            raise _Reject from None
        if value.is_finite():
            return value
    raise _Reject


def _date(raw):
    if isinstance(raw, str) and _DATE.fullmatch(raw):
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            raise _Reject from None
    raise _Reject


def _timestamp(raw):
    if isinstance(raw, str) and _TIMESTAMP.fullmatch(raw):
        try:
            return dt.datetime.fromisoformat(raw)
        except ValueError:
            raise _Reject from None
    raise _Reject


def _boolean(raw):
    if isinstance(raw, bool):
        return raw
    raise _Reject


TYPES = MappingProxyType({spec.name: spec for spec in (
    TypeSpec("text", orderable=True, coerce=_text),
    TypeSpec("integer", orderable=True, coerce=_integer),
    TypeSpec("double", orderable=True, coerce=_double),
    TypeSpec("decimal", orderable=True, coerce=_decimal),
    TypeSpec("date", orderable=True, coerce=_date),
    TypeSpec("timestamp", orderable=True, coerce=_timestamp),
    TypeSpec("boolean", orderable=False, coerce=_boolean),
)})


def coerce_literal(type_name: str, raw: Any, path: str = "") -> TypedLiteral:
    """Coerce a JSON value to a literal of a registered type, or raise InvalidLiteral."""
    try:
        return TypedLiteral(TYPES[type_name].coerce(raw), type_name)
    except _Reject:
        raise InvalidLiteral(f"{raw!r} is not a valid {type_name} literal.", path=path) from None
