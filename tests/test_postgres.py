"""S11: SQL compiled for dialect "postgres", executed on a real Postgres 16 (DESIGN.md §6.3, §8, §10).

Marked `postgres`: excluded from `make test`, run by `make test-pg` (docker-compose.yml). Each test gets
its own schema (tests/conftest.py `pg_schema`), loaded with fixtures/pdf/postgres_schema.sql or the
same fixtures/synthetic/seed.sql that DuckDB loads.

Every expectation is the one the DuckDB suite already uses (fixtures/pdf/expected/*.json,
tests/synthetic_helpers.py, tests/test_synthetic.py, tests/test_dialects.py). No expected value is
Postgres-specific; only the raw-engine division-by-zero tests differ, and they are the reason the
compiler guards pct_change with NULLIF (DESIGN.md §6.3).

Type normalisation: the result comparison (tests/helpers.py `_cell_equal`) compares every number as
float with a 1e-9 tolerance, as it already does on DuckDB. On Postgres that absorbs
  - DECIMAL(8,2) group keys (list_price), which come back as Decimal, and
  - `100.0 * (count - count) / NULLIF(count, 0)` (a count's pct_change), which is NUMERIC -> Decimal on
    Postgres (NUMERIC * BIGINT / BIGINT) and DOUBLE on DuckDB.
Booleans, text, NULL, date and timestamp cells are still compared by exact type and value.
"""
import datetime as dt
import math
from decimal import Decimal

import pytest

from walt_compiler import compile_contract
from walt_compiler.dialects import get_dialect
from walt_compiler.sqlast import Literal
from walt_compiler.types import TypedLiteral
from tests.conftest import SYNTHETIC, run
from tests.helpers import PDF, assert_result, pdf_contract, pdf_expected, pdf_model
from tests.synthetic_helpers import (CONTRACT_EXPECTED, MISSING_PERIOD_CONTRACT, MISSING_PERIOD_EXPECTED,
                                     assert_table, synthetic_contract, synthetic_model)
from tests.test_dialects import ZERO_BASE_CASES
from tests.test_synthetic import FILTERS, GROUPED, by

pytestmark = pytest.mark.postgres

psycopg = pytest.importorskip("psycopg")


def pg_sql(contract, model=None):
    return compile_contract(model or pdf_model(), contract, "postgres")


def synth_sql(contract):
    return compile_contract(synthetic_model(), contract, "postgres")


# === the fixtures themselves are portable ====================================================

def test_pdf_schema_loads_on_postgres(pdf_pg):
    assert pdf_pg.execute("SELECT count(*) FROM fact_sales").fetchone() == (9,)


def test_synthetic_seed_loads_unchanged_on_postgres(synth_pg):
    counts = {t: synth_pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("trips", "cycles", "cycle_models", "makers", "maker_hq", "docks", "dock_audits")}
    assert counts == {"trips": 8, "cycles": 4, "cycle_models": 3, "makers": 2, "maker_hq": 2, "docks": 2,
                      "dock_audits": 2}


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_postgres_golden_sql_itself_executes_to_the_expected_result(pdf_pg, name):
    # Checks the hand-written golden independently of the compiler.
    sql = (PDF / "golden" / "postgres" / f"{name}.sql").read_text()
    cols, rows = run(pdf_pg, sql)
    assert_result(cols, rows, pdf_expected(name))


# === PDF Contracts A / B / C =================================================================

@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_pdf_contract_executes_on_postgres(pdf_pg, name):
    # A: 420 / 710.  B: Total 450 / 710 / 260 / 57.8 (= 100 * 260 / 450).  C: 4 / 3 / 2 + total 8.
    cols, rows = run(pdf_pg, pg_sql(pdf_contract(name)))
    assert_result(cols, rows, pdf_expected(name))


def test_left_join_keeps_orphan_facts_on_postgres(orphan_pg):
    # Same expectation as tests/test_compile.py: O-900 (store S9) survives with region NULL, ordered last.
    cols, rows = run(orphan_pg, pg_sql({"metrics": [{"name": "total_revenue"}], "group_by": ["region"]}))
    assert_result(cols, rows, {"columns": ["region", "total_revenue"],
                               "rows": [["North", 375.0], ["South", 570.0], ["West", 240.0], [None, 40.0]]})


# === synthetic model =========================================================================

@pytest.mark.parametrize("name", list(CONTRACT_EXPECTED))
def test_synthetic_contract_executes_on_postgres(synth_pg, name):
    cols, rows = run(synth_pg, synth_sql(synthetic_contract(name)))
    assert_table(cols, rows, *CONTRACT_EXPECTED[name])


@pytest.mark.parametrize("dimension", list(GROUPED))
def test_group_by_each_dimension_type_on_postgres(synth_pg, dimension):
    # Every dimension type as a group key, 0 to 4 hops; date/timestamp/boolean come back as exact types.
    cols, rows = run(synth_pg, synth_sql(by(dimension)))
    assert_table(cols, rows, [dimension, "trip_count", "fare_total"], GROUPED[dimension])


@pytest.mark.parametrize("flt, count, fare", FILTERS,
                         ids=[f"{f['field']}{f['op']}{f['value']}" for f, _, _ in FILTERS])
def test_filter_literal_of_each_type_on_postgres(synth_pg, flt, count, fare):
    # Every literal the Postgres dialect renders (DATE, TIMESTAMP, TRUE/FALSE, decimal, double, 'Smith''s Quay')
    # is compared against a real Postgres column of that type.
    cols, rows = run(synth_pg, synth_sql({"metrics": [{"name": "trip_count"}, {"name": "fare_total"}],
                                          "filters": [flt]}))
    assert_table(cols, rows, ["trip_count", "fare_total"], [[count, fare]])


def test_count_in_a_missing_compare_period_is_zero_on_postgres(synth_pg):
    # §6.3: count / count_distinct over zero rows are 0; sum is NULL (see MISSING_PERIOD_CONTRACT).
    cols, rows = run(synth_pg, synth_sql(MISSING_PERIOD_CONTRACT))
    assert_table(cols, rows, *MISSING_PERIOD_EXPECTED)


def test_pct_change_over_a_null_base_is_null_not_an_error_on_postgres(synth_pg):
    # Same table as tests/test_edge_cases.py (sum per maker_country, 05 -> 07): NULL / NULL is NULL on
    # Postgres too; only a zero base raises.
    #   DE 4 -> 3: delta -1, pct 100 * -1 / 4 = -25;  NL 8 -> NULL;  NULL-country NULL -> 7
    c = {"metrics": [{"name": "fare_total"}], "group_by": ["maker_country"],
         "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-07"], "primary": "2026-01-07",
                     "outputs": ["values", "delta", "pct_change"]}}
    cols, rows = run(synth_pg, synth_sql(c))
    assert_table(cols, rows, ["maker_country", "fare_total_2026-01-05", "fare_total_2026-01-07",
                              "fare_total_delta", "fare_total_pct_change"],
                 [["DE", 4.0, 3.0, -1.0, -25.0], ["NL", 8.0, None, None, None], [None, None, 7.0, None, None]])


# === literals round-trip through a real Postgres =============================================

@pytest.mark.parametrize("lit, expected", [
    (TypedLiteral("O'Brien", "text"), "O'Brien"),
    (TypedLiteral("C:\\temp", "text"), "C:\\temp"),            # backslash is not an escape character
    (TypedLiteral("Zürich", "text"), "Zürich"),
    (TypedLiteral(2026, "integer"), 2026),
    (TypedLiteral(2.5, "double"), 2.5),
    (TypedLiteral(Decimal("12.50"), "decimal"), Decimal("12.50")),
    (TypedLiteral(dt.date(2026, 2, 1), "date"), dt.date(2026, 2, 1)),
    (TypedLiteral(dt.datetime(2026, 2, 1, 10, 30, 0, 123456), "timestamp"),
     dt.datetime(2026, 2, 1, 10, 30, 0, 123456)),
    (TypedLiteral(True, "boolean"), True),
    (TypedLiteral(False, "boolean"), False),
], ids=lambda x: repr(getattr(x, "value", x)))
def test_postgres_literal_round_trips(pg_schema, lit, expected):
    sql = "SELECT " + get_dialect("postgres").render(Literal(lit))
    (value,), = pg_schema.execute(sql).fetchall()
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        # 2.5 is a NUMERIC constant on Postgres and comes back as Decimal('2.5').
        assert math.isclose(float(value), float(expected))
    else:
        assert type(value) is type(expected) and value == expected


# === division by zero: a zero pct_change base gives NULL (DESIGN.md §6.3) ======================
# Why the compiler guards with NULLIF: observed on Postgres 16.14, every raw numeric `/` by zero
# (integer, NUMERIC and DOUBLE PRECISION, including 0 / 0) raises SQLSTATE 22012 division_by_zero, and
# the whole query fails, not just the offending row.

@pytest.mark.parametrize("sql", [
    "SELECT 1 / 0",
    "SELECT 1.0 / 0.0",
    "SELECT 0.0 / 0.0",
    "SELECT CAST(1 AS DOUBLE PRECISION) / 0",
    "SELECT CAST(0 AS DOUBLE PRECISION) / CAST(0 AS DOUBLE PRECISION)",
    "SELECT 100.0 * (CAST(1 AS BIGINT) - CAST(0 AS BIGINT)) / CAST(0 AS BIGINT)",   # unguarded count pct
])
def test_postgres_raw_division_by_zero_raises_which_is_why_the_compiler_guards(pg_schema, sql):
    with pytest.raises(psycopg.errors.DivisionByZero) as exc:
        pg_schema.execute(sql)
    assert exc.value.sqlstate == "22012"


@pytest.mark.parametrize("sql", [
    "SELECT CAST(NULL AS DOUBLE PRECISION) / 0",
    "SELECT 100.0 * (CAST(5 AS DOUBLE PRECISION) - CAST(0 AS DOUBLE PRECISION)) / NULLIF(CAST(0 AS DOUBLE PRECISION), 0)",
    "SELECT 100.0 * (CAST(1 AS BIGINT) - CAST(0 AS BIGINT)) / NULLIF(CAST(0 AS BIGINT), 0)",
], ids=["null-over-zero", "guarded-double", "guarded-count"])
def test_postgres_raw_null_denominator_is_null(pg_schema, sql):
    assert pg_schema.execute(sql).fetchall() == [(None,)]


@pytest.fixture
def zero_pg(synth_pg):
    synth_pg.execute((SYNTHETIC / "zero_fare_rows.sql").read_text())
    return synth_pg


@pytest.mark.parametrize("case", list(ZERO_BASE_CASES))
def test_postgres_compiled_pct_change_over_a_zero_base_is_null(zero_pg, case):
    # Same cases and expectations as DuckDB (tests/test_dialects.py ZERO_BASE_CASES): the query succeeds
    # and the zero-base cell is NULL:  fare 0.0 -> 5.0,  fare 0.0 -> 0.0,  count 0 (missing period) -> 1.
    contract, columns, expected = ZERO_BASE_CASES[case]
    cols, rows = run(zero_pg, synth_sql(contract))
    assert_table(cols, rows, columns, expected)


# === identifier length: 63 bytes is Postgres's limit (DESIGN.md §6.5) ========================

@pytest.mark.parametrize("alias", ["a" * 63, "ü" * 31 + "a"], ids=["ascii-63-bytes", "utf8-63-bytes-32-chars"])
def test_a_63_byte_output_name_round_trips_exactly(pdf_pg, alias):
    # Exactly at the limit: compiles for postgres, and Postgres returns the column name untruncated.
    assert len(alias.encode("utf-8")) == 63
    cols, rows = run(pdf_pg, pg_sql({"metrics": [{"name": "total_revenue", "as": alias}]}))
    assert cols == [alias]
    assert rows == [(1160.0,)]   # all 9 PDF lines: 100+200+150+120+80+300+50+70+90
