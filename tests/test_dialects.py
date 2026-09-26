"""S11: the dialect seam without a Postgres server (DESIGN.md §6.3, §8, §9, §10).

- the `postgres` dialect is registered, with FILTER and GROUPING SETS capabilities
- UnknownDialect suggests, never auto-corrects
- Postgres literal spelling and golden SQL for Contracts A/B/C
- the CASE WHEN fallback for a dialect with `supports_aggregate_filter = False`, executed on DuckDB
- DuckDB's native division-by-zero result, pinned from the real engine

The tests that need a live Postgres are in test_postgres.py (marker `postgres`, `make test-pg`).
"""
import datetime as dt
import math
from decimal import Decimal

import pytest

from walt_compiler import compile_contract
from walt_compiler.contract import parse_contract
from walt_compiler.dialects import get_dialect
from walt_compiler.dialects.base import Dialect
from walt_compiler.errors import UnknownDialect
from walt_compiler.lower import lower
from walt_compiler.model import build_catalog
from walt_compiler.resolve import resolve
from walt_compiler.sqlast import Literal
from walt_compiler.types import TypedLiteral
from tests.conftest import run
from tests.helpers import PDF, assert_result, pdf_contract, pdf_expected, pdf_model
from tests.synthetic_helpers import (ALL_METRICS, CONTRACT_EXPECTED, MISSING_PERIOD_CONTRACT,  # noqa: F401
                                     MISSING_PERIOD_EXPECTED, SYNTHETIC, assert_table, synth_duckdb,
                                     synthetic_contract, synthetic_model)


def golden(dialect, name):
    return (PDF / "golden" / dialect / f"{name}.sql").read_text().rstrip("\n")


# === registry ================================================================================

def test_postgres_is_a_registered_dialect_with_filter_and_grouping_sets():
    # Postgres has had FILTER on aggregates since 9.4 and GROUPING SETS / GROUPING() since 9.5.
    pg = get_dialect("postgres")
    assert isinstance(pg, Dialect)
    assert pg.name == "postgres"
    assert pg.supports_aggregate_filter is True
    assert pg.supports_grouping_sets is True


@pytest.mark.parametrize("name, suggestions", [
    ("postgress", ("postgres",)),      # 1 edit / 9
    ("postgre", ("postgres",)),        # 1 edit / 8
    ("Postgres", ("postgres",)),       # case-insensitive score 0, but still not auto-corrected
    ("POSTGRES", ("postgres",)),
    ("duckdbb", ("duckdb",)),          # postgres is far from duckdb: not suggested alongside
    ("DuckDB", ("duckdb",)),
    ("mysql", ()),
    ("pg", ()),                        # 6 edits / 8 = 0.75 > 0.4
])
def test_unknown_dialect_is_suggested_never_auto_corrected(name, suggestions):
    with pytest.raises(UnknownDialect) as exc:
        compile_contract(pdf_model(), pdf_contract("a"), name)
    assert exc.value.code == "UnknownDialect"
    assert exc.value.suggestions == suggestions


def test_unknown_dialect_message_offers_the_suggestion():
    with pytest.raises(UnknownDialect) as exc:
        compile_contract(pdf_model(), pdf_contract("a"), "postgress")
    assert "Did you mean: postgres?" in str(exc.value)


# === Postgres spelling =======================================================================

def pg_lit(value, type_name):
    return get_dialect("postgres").render(Literal(TypedLiteral(value, type_name)))


@pytest.mark.parametrize("value, type_name, sql", [
    ("Online", "text", "'Online'"),
    ("O'Brien", "text", "'O''Brien'"),
    # standard_conforming_strings is on by default since Postgres 9.1: a backslash is an ordinary
    # character in '...', so it is not doubled (and E'...' is never used).
    ("C:\\temp", "text", "'C:\\temp'"),
    ("Zürich", "text", "'Zürich'"),
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
def test_postgres_literals(value, type_name, sql):
    assert pg_lit(value, type_name) == sql


def test_postgres_identifiers_are_always_quoted_and_escaped():
    pg = get_dialect("postgres")
    assert pg.quote_ident("date") == '"date"'
    assert pg.quote_ident('a"b') == '"a""b"'


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_pdf_contract_golden_sql_postgres(name):
    assert compile_contract(pdf_model(), pdf_contract(name), "postgres") == golden("postgres", name)


def test_postgres_compile_is_deterministic():
    first = compile_contract(pdf_model(), pdf_contract("b"), "postgres")
    assert all(compile_contract(pdf_model(), pdf_contract("b"), "postgres") == first for _ in range(100))


# === CASE WHEN fallback (supports_aggregate_filter = False) ==================================

class NoFilterDuckDB(type(get_dialect("duckdb"))):
    """Test-only dialect: DuckDB spelling, but declares no FILTER clause on aggregates.

    It is not registered: like the no-GROUPING-SETS dialect in test_totals.py, it renders the
    dialect-free SQL AST produced by the pipeline (parse -> catalog -> resolve -> lower).
    """
    name = "duckdb_no_filter"
    supports_aggregate_filter = False


NO_FILTER = NoFilterDuckDB()


def compile_no_filter(contract, model=None):
    return NO_FILTER.render(lower(resolve(parse_contract(contract), build_catalog(model or pdf_model()))))


# Hand-written from the DuckDB goldens: each `AGG(arg) FILTER (WHERE cond)` becomes
# `AGG(CASE WHEN cond THEN arg END)`; everything else is unchanged.
NO_FILTER_A = """\
SELECT
  "online_rev",
  "total_revenue"
FROM (
  SELECT
    SUM(CASE WHEN "fact_sales"."channel" = 'Online' THEN "fact_sales"."revenue" END) AS "online_rev",
    SUM("fact_sales"."revenue") AS "total_revenue"
  FROM "fact_sales"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" = 2026
) AS "agg"
""".rstrip("\n")

NO_FILTER_B = """\
SELECT
  "region",
  "total_revenue_2025",
  "total_revenue_2026",
  "total_revenue_2026" - "total_revenue_2025" AS "total_revenue_delta",
  100.0 * ("total_revenue_2026" - "total_revenue_2025") / NULLIF("total_revenue_2025", 0) AS "total_revenue_pct_change",
  "is_total"
FROM (
  SELECT
    "dim_store"."region" AS "region",
    SUM(CASE WHEN "dim_calendar"."fiscal_year" = 2025 THEN "fact_sales"."revenue" END) AS "total_revenue_2025",
    SUM(CASE WHEN "dim_calendar"."fiscal_year" = 2026 THEN "fact_sales"."revenue" END) AS "total_revenue_2026",
    GROUPING("dim_store"."region") = 1 AS "is_total"
  FROM "fact_sales"
  LEFT JOIN "dim_store" ON "fact_sales"."store_id" = "dim_store"."store_id"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" IN (2025, 2026)
  GROUP BY GROUPING SETS (("dim_store"."region"), ())
) AS "agg"
ORDER BY "is_total" ASC, "region" ASC NULLS LAST"""


@pytest.mark.parametrize("name, expected", [("a", NO_FILTER_A), ("b", NO_FILTER_B)])
def test_fallback_sql_uses_case_when_inside_the_aggregate(name, expected):
    sql = compile_no_filter(pdf_contract(name))
    assert sql == expected
    assert "FILTER" not in sql


def test_fallback_contract_without_conditional_measures_is_unchanged():
    # Contract C has no per-metric filter and no compare: nothing to rewrite.
    assert compile_no_filter(pdf_contract("c")) == golden("duckdb", "c")


def test_fallback_puts_metric_filter_and_period_in_one_case_condition():
    # Condition order is the metric's own filter AND the period (DESIGN.md §6.3), as in the FILTER form.
    c = {"metrics": [{"name": "unique_riders", "as": "member_riders",
                      "filters": [{"field": "member", "op": "=", "value": True}]}],
         "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-06"], "primary": "2026-01-06",
                     "outputs": ["values"]}}
    sql = compile_no_filter(c, synthetic_model())
    assert ('COUNT(DISTINCT CASE WHEN "trips"."is_member" = TRUE AND "trips"."trip_day" = DATE \'2026-01-05\' '
            'THEN "trips"."rider_ref" END) AS "member_riders_2026-01-05"') in sql
    assert ('COUNT(DISTINCT CASE WHEN "trips"."is_member" = TRUE AND "trips"."trip_day" = DATE \'2026-01-06\' '
            'THEN "trips"."rider_ref" END) AS "member_riders_2026-01-06"') in sql
    assert "FILTER" not in sql


# --- executed on DuckDB: same answers as the FILTER form --------------------------------------

@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_fallback_executes_to_the_pdf_expected_results(pdf_duckdb, name):
    cols, rows = run(pdf_duckdb, compile_no_filter(pdf_contract(name)))
    assert_result(cols, rows, pdf_expected(name))


@pytest.mark.parametrize("name", list(CONTRACT_EXPECTED))
def test_fallback_executes_to_the_synthetic_expected_results(synth_duckdb, name):
    # fare_variants_by_day = per-metric filters (1- and 3-hop, two ANDed); riders_by_electric_compare =
    # compare + count_distinct + avg + totals; every_aggregation_by_country_member has no conditional measure.
    cols, rows = run(synth_duckdb, compile_no_filter(synthetic_contract(name), synthetic_model()))
    assert_table(cols, rows, *CONTRACT_EXPECTED[name])


def test_fallback_count_in_a_missing_compare_period_is_zero(synth_duckdb):
    # §6.3: count / count_distinct over zero rows are 0, sum is NULL -- CASE WHEN agrees with FILTER.
    cols, rows = run(synth_duckdb, compile_no_filter(MISSING_PERIOD_CONTRACT, synthetic_model()))
    assert_table(cols, rows, *MISSING_PERIOD_EXPECTED)


def test_filter_form_count_in_a_missing_compare_period_is_zero(synth_duckdb):
    # The same table through the registered DuckDB dialect (FILTER form).
    cols, rows = run(synth_duckdb, compile_contract(synthetic_model(), MISSING_PERIOD_CONTRACT, "duckdb"))
    assert_table(cols, rows, *MISSING_PERIOD_EXPECTED)


_EVERY_AGG_COMPARE = {
    "metrics": [{"name": n} for n in ALL_METRICS]
    + [{"name": "fare_total", "as": "member_fare", "filters": [{"field": "member", "op": "=", "value": True}]},
       {"name": "trip_count", "as": "nl_trips", "filters": [{"field": "maker_country", "op": "=", "value": "NL"}]}],
    "group_by": ["district"],
    "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-06"], "primary": "2026-01-06",
                "outputs": ["values", "delta", "pct_change"]},
    "totals": "grand",
}


@pytest.mark.parametrize("contract", [
    _EVERY_AGG_COMPARE,
    {"metrics": [{"name": "avg_fare", "filters": [{"field": "electric", "op": "=", "value": False}]},
                 {"name": "shortest_km", "filters": [{"field": "hq_city", "op": "=", "value": "Utrecht"}]},
                 {"name": "longest_km", "filters": [{"field": "list_price", "op": ">=", "value": "1499.50"}]}],
     "group_by": ["member"], "totals": "grand"},
], ids=["compare-every-aggregation-and-filters", "min-max-avg-filters"])
def test_fallback_matches_the_filter_form_row_for_row(synth_duckdb, contract):
    # pct_change included: zero bases (count 0 in a missing period) must be NULL in both forms, never NaN.
    model = synthetic_model()
    want_cols, want_rows = run(synth_duckdb, compile_contract(model, contract, "duckdb"))
    cols, rows = run(synth_duckdb, compile_no_filter(contract, model))
    assert "FILTER" not in compile_no_filter(contract, model)
    assert_table(cols, rows, want_cols, [list(r) for r in want_rows])


# === division by zero: a zero pct_change base gives NULL (DESIGN.md §6.3) ======================
# pct_change = 100.0 * (primary - other) / NULLIF(other, 0) on every dialect.
#
# Why the compiler guards: the raw engines disagree. Observed on DuckDB 1.5.5 with default settings
# (ieee_floating_point_ops = true), `/` is IEEE-754 division for every numeric type: x / 0 is +inf or
# -inf and 0 / 0 is NaN (not valid JSON). Postgres raises division_by_zero instead (test_postgres.py).

@pytest.mark.parametrize("sql, expected", [
    ("SELECT 1.0 / 0.0", math.inf),
    ("SELECT 1 / 0", math.inf),                                     # integer `/` is float division too
    ("SELECT CAST(1 AS DOUBLE) / 0", math.inf),
    ("SELECT CAST(-1 AS DOUBLE) / 0", -math.inf),
    ("SELECT 100.0 * (CAST(1 AS BIGINT) - CAST(0 AS BIGINT)) / CAST(0 AS BIGINT)", math.inf),  # unguarded count pct
    ("SELECT 0.0 / 0.0", math.nan),
    ("SELECT CAST(0 AS DOUBLE) / CAST(0 AS DOUBLE)", math.nan),
    ("SELECT CAST(NULL AS DOUBLE) / 0", None),                      # NULL wins over zero
    ("SELECT 100.0 * (CAST(5 AS DOUBLE) - CAST(0 AS DOUBLE)) / NULLIF(CAST(0 AS DOUBLE), 0)", None),  # the guard
    ("SELECT 100.0 * (CAST(1 AS BIGINT) - CAST(0 AS BIGINT)) / NULLIF(CAST(0 AS BIGINT), 0)", None),
])
def test_duckdb_raw_division_by_zero_is_why_the_compiler_guards(sql, expected):
    import duckdb
    (value,), = duckdb.connect(":memory:").execute(sql).fetchall()
    if expected is None:
        assert value is None
    elif math.isnan(expected):
        assert isinstance(value, float) and math.isnan(value)
    else:
        assert value == expected


@pytest.fixture
def zero_duckdb(synth_duckdb):
    synth_duckdb.execute((SYNTHETIC / "zero_fare_rows.sql").read_text())
    return synth_duckdb


def fare_compare(base_day, primary_day):
    return {"metrics": [{"name": "fare_total"}],
            "compare": {"dimension": "trip_date", "periods": [base_day, primary_day], "primary": primary_day,
                        "outputs": ["values", "delta", "pct_change"]}}


# The same three zero-base cases run on Postgres in test_postgres.py with the same expectations.
ZERO_BASE_CASES = {
    # fixtures/synthetic/zero_fare_rows.sql: 08 = 0.0, 09 = 5.0 -> delta 5.0, pct 100 * 5 / NULL = NULL
    "fare-0-to-5": (fare_compare("2026-01-08", "2026-01-09"),
                    ["fare_total_2026-01-08", "fare_total_2026-01-09", "fare_total_delta", "fare_total_pct_change"],
                    [[0.0, 5.0, 5.0, None]]),
    # 08 = 0.0, 10 = 0.0 -> delta 0.0, pct 100 * 0 / NULL = NULL
    "fare-0-to-0": (fare_compare("2026-01-08", "2026-01-10"),
                    ["fare_total_2026-01-08", "fare_total_2026-01-10", "fare_total_delta", "fare_total_pct_change"],
                    [[0.0, 0.0, 0.0, None]]),
    # trip_count by maker_country, 05 -> 07 (see MISSING_PERIOD_CONTRACT); a count over no rows is 0:
    #   DE 1 -> 1: pct 0;  NL 2 -> 0: pct 100 * -2 / 2 = -100;  NULL-country 0 -> 1: pct 100 * 1 / NULL = NULL
    "count-0-to-1": ({"metrics": [{"name": "trip_count"}], "group_by": ["maker_country"],
                      "compare": {**MISSING_PERIOD_CONTRACT["compare"], "outputs": ["values", "pct_change"]}},
                     ["maker_country", "trip_count_2026-01-05", "trip_count_2026-01-07", "trip_count_pct_change"],
                     [["DE", 1, 1, 0.0], ["NL", 2, 0, -100.0], [None, 0, 1, None]]),
}


@pytest.mark.parametrize("form", ["filter", "case_when"])
@pytest.mark.parametrize("case", list(ZERO_BASE_CASES))
def test_duckdb_compiled_pct_change_over_a_zero_base_is_null(zero_duckdb, case, form):
    # The query succeeds and the zero-base cell is NULL, never inf / NaN, in both the FILTER and CASE WHEN forms.
    contract, columns, expected = ZERO_BASE_CASES[case]
    model = synthetic_model()
    sql = compile_contract(model, contract, "duckdb") if form == "filter" else compile_no_filter(contract, model)
    cols, rows = run(zero_duckdb, sql)
    assert_table(cols, rows, columns, expected)


def test_pct_change_denominator_is_guarded_with_nullif():
    sql = compile_contract(synthetic_model(), ZERO_BASE_CASES["count-0-to-1"][0], "duckdb")
    assert ('100.0 * ("trip_count_2026-01-07" - "trip_count_2026-01-05") / NULLIF("trip_count_2026-01-05", 0) '
            'AS "trip_count_pct_change"') in sql
