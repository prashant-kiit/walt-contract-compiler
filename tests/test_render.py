"""S7: SQL AST rendering and the dialect seam (DESIGN.md §8)."""
import datetime as dt
from decimal import Decimal

import pytest

from walt_compiler.dialects import get_dialect
from walt_compiler.dialects.base import Dialect
from walt_compiler.errors import UnknownDialect, UnsupportedFeature
from walt_compiler.options import CompileOptions
from walt_compiler.sqlast import Aggregate, BinOp, BoolOp, Column, InList, Literal, Not
from walt_compiler.types import TypedLiteral

DUCK = get_dialect("duckdb")
A, B, C = Column(None, "a"), Column(None, "b"), Column(None, "c")
X = Column("t", "x")
COND = BinOp("=", Column("t", "k"), Literal(TypedLiteral("v", "text")))


def lit(value, type_name):
    return DUCK.render(Literal(TypedLiteral(value, type_name)))


# --- identifiers and literals ----------------------------------------------------------------

def test_identifiers_are_always_quoted_and_escaped():
    assert DUCK.render(Column("fact_sales", "date")) == '"fact_sales"."date"'
    assert DUCK.render(Column(None, 'a"b')) == '"a""b"'


@pytest.mark.parametrize("value, type_name, sql", [
    ("Online", "text", "'Online'"),
    ("O'Brien", "text", "'O''Brien'"),
    (2026, "integer", "2026"),
    (-7, "integer", "-7"),
    (2.5, "double", "2.5"),
    (100.0, "double", "100.0"),
    (Decimal("12.50"), "decimal", "12.50"),
    (Decimal("1E+2"), "decimal", "100"),
    (dt.date(2026, 2, 1), "date", "DATE '2026-02-01'"),
    (dt.datetime(2026, 2, 1, 10, 30), "timestamp", "TIMESTAMP '2026-02-01 10:30:00'"),
    (dt.datetime(2026, 2, 1, 10, 30, 0, 123456), "timestamp", "TIMESTAMP '2026-02-01 10:30:00.123456'"),
    (True, "boolean", "TRUE"),
    (False, "boolean", "FALSE"),
])
def test_literals(value, type_name, sql):
    assert lit(value, type_name) == sql


# --- expressions and precedence --------------------------------------------------------------

def test_pct_change_keeps_only_needed_parentheses():
    pct = BinOp("/", BinOp("*", Literal(TypedLiteral(100.0, "double")), BinOp("-", A, B)), B)
    assert DUCK.render(pct) == '100.0 * ("a" - "b") / "b"'


def test_right_operand_of_minus_is_parenthesised():
    assert DUCK.render(BinOp("-", A, BinOp("-", B, C))) == '"a" - ("b" - "c")'
    assert DUCK.render(BinOp("-", BinOp("-", A, B), C)) == '"a" - "b" - "c"'


def test_not_equal_renders_as_standard_sql():
    assert DUCK.render(BinOp("!=", A, B)) == '"a" <> "b"'


def test_boolean_operators():
    or_ = BoolOp("OR", (BinOp("=", A, B), BinOp("=", A, C)))
    assert DUCK.render(BoolOp("AND", (or_, BinOp("<", B, C)))) == '("a" = "b" OR "a" = "c") AND "b" < "c"'
    assert DUCK.render(Not(or_)) == 'NOT ("a" = "b" OR "a" = "c")'


def test_in_list():
    items = (Literal(TypedLiteral(1, "integer")), Literal(TypedLiteral(2, "integer")))
    assert DUCK.render(InList(A, items)) == '"a" IN (1, 2)'


# --- aggregates and the FILTER capability ----------------------------------------------------

def test_aggregates_with_filter_clause():
    assert DUCK.render(Aggregate("SUM", X, False, True, None)) == 'SUM("t"."x")'
    assert DUCK.render(Aggregate("COUNT", X, True, True, None)) == 'COUNT(DISTINCT "t"."x")'
    assert DUCK.render(Aggregate("SUM", X, False, True, COND)) == \
        'SUM("t"."x") FILTER (WHERE "t"."k" = \'v\')'
    assert DUCK.render(Aggregate("COUNT", X, True, True, COND)) == \
        'COUNT(DISTINCT "t"."x") FILTER (WHERE "t"."k" = \'v\')'


class NoFilterDialect(Dialect):
    """Test-only dialect: exercises the CASE WHEN fallback through the capability flag."""
    name = "no_filter"
    supports_aggregate_filter = False


def test_case_when_fallback_for_dialects_without_filter():
    d = NoFilterDialect()
    assert d.render(Aggregate("SUM", X, False, True, COND)) == \
        'SUM(CASE WHEN "t"."k" = \'v\' THEN "t"."x" END)'
    assert d.render(Aggregate("COUNT", X, True, True, COND)) == \
        'COUNT(DISTINCT CASE WHEN "t"."k" = \'v\' THEN "t"."x" END)'


def test_case_when_fallback_refuses_aggregates_that_count_nulls():
    with pytest.raises(UnsupportedFeature):
        NoFilterDialect().render(Aggregate("COUNT_WITH_NULLS", X, False, False, COND))


# --- registry --------------------------------------------------------------------------------

def test_unknown_dialect_is_suggested():
    with pytest.raises(UnknownDialect) as exc:
        get_dialect("duckdbb")
    assert exc.value.suggestions == ("duckdb",)


def test_unknown_dialect_suggestions_respect_options():
    with pytest.raises(UnknownDialect) as exc:
        get_dialect("duckdbb", CompileOptions(suggestion_threshold=0.0))
    assert exc.value.suggestions == ()
