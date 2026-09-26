"""Declarative shape rules for JSON documents (DESIGN.md §3, syntactic analysis).

The model and contract shapes are described as data (Obj / Arr / Str / AnyValue) and checked by
one small interpreter, so adding a key to the vocabulary is adding a rule entry, not an `if`.
Errors carry a JSON pointer to the offending node and, for unknown keys and enum values,
"did you mean" suggestions.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from walt_compiler.errors import CompilerError
from walt_compiler.options import CompileOptions
from walt_compiler.suggest import suggest


@dataclass(frozen=True)
class Str:
    """A non-empty string, optionally restricted to an enum of allowed values."""
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AnyValue:
    """Any JSON value; meaning is checked later (e.g. filter values, by operator and type)."""


@dataclass(frozen=True)
class Arr:
    item: Any
    min_items: int = 0
    unique: bool = False


@dataclass(frozen=True)
class Field:
    rule: Any
    required: bool = True


@dataclass(frozen=True)
class Obj:
    fields: Mapping[str, Field]


def pointer(*parts) -> str:
    """RFC 6901 JSON pointer for a path of keys and indexes."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def kind_of(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate(value, rule, *, error: type[CompilerError], options: CompileOptions, path: tuple = ()) -> None:
    """Raise `error` at the first node of `value` that breaks `rule`."""
    def fail(message, at=path, suggestions=()):
        raise error(message, path=pointer(*at), suggestions=suggestions)

    def near(key, candidates):
        return suggest(key, candidates, options.suggestion_threshold, options.max_suggestions)

    def expect(kind, python_type):
        if not isinstance(value, python_type):
            fail(f"expected {kind}, got {kind_of(value)}.")

    if isinstance(rule, AnyValue):
        return

    if isinstance(rule, Str):
        expect("string", str)
        if value == "":
            fail("must not be empty.")
        if rule.enum is not None and value not in rule.enum:
            fail(f"{value!r} is not one of: {', '.join(rule.enum)}.", suggestions=near(value, rule.enum))
        return

    if isinstance(rule, Arr):
        expect("array", list)
        if len(value) < rule.min_items:
            fail(f"must contain at least {rule.min_items} item(s).")
        seen = []
        for i, item in enumerate(value):
            if rule.unique and item in seen:
                fail(f"duplicate item {item!r}.", at=path + (i,))
            seen.append(item)
            validate(item, rule.item, error=error, options=options, path=path + (i,))
        return

    if isinstance(rule, Obj):
        expect("object", dict)
        for key in value:
            if key not in rule.fields:
                fail(f"unknown key {key!r}.", at=path + (key,), suggestions=near(key, rule.fields))
        for key, field in rule.fields.items():
            if key in value:
                validate(value[key], field.rule, error=error, options=options, path=path + (key,))
            elif field.required:
                fail(f"missing required key {key!r}.", at=path + (key,))
        return

    raise TypeError(f"unknown rule {rule!r}")
