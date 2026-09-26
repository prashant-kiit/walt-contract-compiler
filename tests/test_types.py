"""S2: dimension-type registry and literal coercion (DESIGN.md §5)."""
import datetime as dt
from decimal import Decimal

import pytest

from walt_compiler.errors import InvalidLiteral
from walt_compiler.types import TYPES, TypedLiteral, coerce_literal


def test_registry_holds_exactly_the_designed_types():
    assert sorted(TYPES) == ["boolean", "date", "decimal", "double", "integer", "text", "timestamp"]


def test_registry_is_read_only():
    with pytest.raises(TypeError):
        TYPES["uuid"] = object()


def test_only_boolean_is_unordered():
    assert [name for name, spec in sorted(TYPES.items()) if not spec.orderable] == ["boolean"]


@pytest.mark.parametrize("type_name, raw, value", [
    ("text", "Online", "Online"),
    ("text", "", ""),
    ("text", "O'Brien", "O'Brien"),
    ("integer", 2026, 2026),
    ("integer", "2025", 2025),          # contract B writes periods as strings
    ("integer", "-7", -7),
    ("integer", 2026.0, 2026),          # integral float from JSON
    ("double", 1.5, 1.5),
    ("double", 3, 3.0),
    ("double", "2.25", 2.25),
    ("decimal", "12.50", Decimal("12.50")),
    ("decimal", 7, Decimal("7")),
    ("decimal", 0.1, Decimal("0.1")),   # via str(), not the binary float expansion
    ("date", "2026-02-01", dt.date(2026, 2, 1)),
    ("timestamp", "2026-02-01T10:30:00", dt.datetime(2026, 2, 1, 10, 30)),
    ("timestamp", "2026-02-01 10:30", dt.datetime(2026, 2, 1, 10, 30)),
    ("timestamp", "2026-02-01T10:30:00.123456", dt.datetime(2026, 2, 1, 10, 30, 0, 123456)),
    ("boolean", True, True),
    ("boolean", False, False),
])
def test_coerces_valid_literals(type_name, raw, value):
    lit = coerce_literal(type_name, raw)
    assert lit == TypedLiteral(value, type_name)
    assert type(lit.value) is type(value)


@pytest.mark.parametrize("type_name, raw", [
    ("text", 2026),                 # no silent number -> text
    ("text", None),
    ("integer", True),              # bool is not an integer
    ("integer", 2026.5),
    ("integer", "20.5"),
    ("integer", " 2025"),
    ("integer", "abc"),
    ("integer", None),
    ("double", "abc"),
    ("double", "nan"),
    ("double", "inf"),
    ("double", False),
    ("decimal", "NaN"),
    ("decimal", "Infinity"),
    ("decimal", "1_000"),           # Python-only digit separators
    ("double", " 2.5"),             # no surrounding whitespace
    ("decimal", "abc"),
    ("decimal", True),
    ("date", "2026-02-30"),         # not a real day
    ("date", "20260201"),           # only YYYY-MM-DD
    ("date", "2026-02-01T00:00"),
    ("date", 20260201),
    ("timestamp", "2026-02-01"),    # needs a time part
    ("timestamp", "2026-02-01T10:30:00+02:00"),  # no time zones
    ("timestamp", 1767225600),
    ("boolean", 1),                 # 1 is not a bool
    ("boolean", "true"),
    ("boolean", None),
])
def test_rejects_invalid_literals(type_name, raw):
    with pytest.raises(InvalidLiteral):
        coerce_literal(type_name, raw)


def test_invalid_literal_reports_path_and_type():
    with pytest.raises(InvalidLiteral) as exc:
        coerce_literal("integer", "abc", path="/filters/0/value")
    assert exc.value.path == "/filters/0/value"
    assert "integer" in exc.value.message
    assert "'abc'" in exc.value.message
