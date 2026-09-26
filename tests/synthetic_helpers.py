"""Helpers for the synthetic bike-share model (fixtures/synthetic, DESIGN.md §2.5 and §10).

Joined view of fixtures/synthetic/seed.sql, used by every hand-computed expectation in
test_synthetic.py, test_combinations.py and test_edge_cases.py:

  trip day        start    rider fare km  member | cycle electric kg   | year price   | maker country hq      | district
  T01 2026-01-05  08:00    101   3    2.5 T      | C1    F        14.5 | 2023  899.00 | Velo  NL      Utrecht | Harbor
  T02 2026-01-05  09:30    102   5    6.0 F      | C2    T        22.0 | 2025 1499.50 | Velo  NL      Utrecht | Harbor
  T03 2026-01-05  18:15    101   4    4.0 T      | C3    T        22.0 | 2024 1499.50 | Rad   DE      Berlin  | Smith's Quay
  T04 2026-01-06  07:45    103   NULL 1.5 T      | C1    F        14.5 | 2023  899.00 | Velo  NL      Utrecht | Smith's Quay
  T05 2026-01-06  12:00    102   6    6.0 F      | C2    T        22.0 | 2025 1499.50 | Velo  NL      Utrecht | Smith's Quay
  T06 2026-01-06  20:00    NULL  2    1.0 F      | C4    F        12.0 | NULL NULL    | NULL  NULL    NULL    | Harbor
  T07 2026-01-07  10:00    101   7    8.0 T      | C9    NULL     NULL | NULL NULL    | NULL  NULL    NULL    | Harbor
  T08 2026-01-07  11:11:11 103   3    3.0 F      | C3    T        22.0 | 2024 1499.50 | Rad   DE      Berlin  | NULL (D9)

Grand aggregates over all 8 trips:
  fare_total 30 (T04 NULL)   trip_count 8   paid_trips 7   unique_riders 3 {101,102,103}
  shortest_km 1.0 (T06)      longest_km 8.0 (T07)          avg_fare 30 / 7
"""
import datetime as dt
from decimal import Decimal

import duckdb
import pytest

from walt_compiler import compile_contract
from tests.helpers import FIXTURES, PDF, _cell_equal, load_json

SYNTHETIC = FIXTURES / "synthetic"

ALL_METRICS = ["fare_total", "trip_count", "paid_trips", "unique_riders", "shortest_km", "longest_km", "avg_fare"]


def synthetic_model() -> dict:
    return load_json(SYNTHETIC / "semantic_model.json")


def synthetic_contract(name: str) -> dict:
    return load_json(SYNTHETIC / "contracts" / f"{name}.json")


def pdf_model_raw() -> dict:
    return load_json(PDF / "semantic_model.json")


@pytest.fixture
def synth_duckdb():
    """In-memory DuckDB loaded with the synthetic seed."""
    con = duckdb.connect(":memory:")
    con.execute((SYNTHETIC / "seed.sql").read_text())
    yield con
    con.close()


def compile_synth(contract: dict, dialect: str = "duckdb", model: dict | None = None) -> str:
    return compile_contract(model or synthetic_model(), contract, dialect)


def execute(con, contract: dict, model: dict | None = None):
    cur = con.execute(compile_synth(contract, model=model))
    return [d[0] for d in cur.description], cur.fetchall()


def fails(contract: dict, error, model: dict | None = None):
    """Compile through the public entry point and return the raised error."""
    with pytest.raises(error) as exc:
        compile_synth(contract, model=model)
    assert exc.value.code == error.__name__
    return exc.value


def _cell(actual, expected) -> bool:
    # date / datetime group keys must come back as exactly that type and value.
    if isinstance(expected, (dt.date, dt.datetime)):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, Decimal):
        return _cell_equal(actual, float(expected))
    return _cell_equal(actual, expected)


def assert_table(columns, rows, expected_columns, expected_rows) -> None:
    """Like tests.helpers.assert_result, plus exact date/timestamp cells. Row order matters."""
    assert list(columns) == expected_columns
    assert len(rows) == len(expected_rows), f"row count {len(rows)} != {len(expected_rows)}: {rows}"
    for i, (got, want) in enumerate(zip(rows, expected_rows)):
        assert len(got) == len(want), f"row {i}: {got} vs {want}"
        for j, (a, e) in enumerate(zip(got, want)):
            assert _cell(a, e), f"row {i} col {expected_columns[j]!r}: got {a!r}, want {e!r}"


# --- expected results of fixtures/synthetic/contracts/*.json ---------------------------------
# Shared by the DuckDB suite (test_combinations.py) and the Postgres suite (test_postgres.py), so
# both engines are held to the same hand-computed table. {contract name: (columns, rows)}.

_D5, _D6, _D7 = dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 7)

CONTRACT_EXPECTED = {
    # electric F: T01 (05, r101, 3), T04 (06, r103, NULL), T06 (06, rider NULL, 2)
    # electric T: T02 (05, r102, 5), T03 (05, r101, 4), T05 (06, r102, 6)
    # unique_riders  F: 1 -> 1 (NULL rider ignored), delta 0, pct 0
    #                T: {102,101} = 2 -> {102} = 1, delta -1, pct 100 * -1 / 2 = -50
    #            total: {101,102} = 2 (not 1 + 2 = 3) -> {103,102} = 2, delta 0, pct 0
    # avg_fare       F: 3 -> 2 / 1 = 2 (NULL fare ignored), delta -1, pct 100 * -1 / 3
    #                T: (5 + 4) / 2 = 4.5 -> 6, delta 1.5, pct 100 * 1.5 / 4.5
    #            total: 12 / 3 = 4 (not (3 + 4.5) / 2 = 3.75) -> (2 + 6) / 2 = 4, delta 0, pct 0
    # T07 (the only NULL-electric trip) is on 2026-01-07, outside both periods.
    "riders_by_electric_compare": (
        ["electric",
         "unique_riders_2026-01-05", "unique_riders_2026-01-06", "unique_riders_delta", "unique_riders_pct_change",
         "avg_fare_2026-01-05", "avg_fare_2026-01-06", "avg_fare_delta", "avg_fare_pct_change",
         "is_total"],
        [[False, 1, 1, 0, 0.0, 3.0, 2.0, -1.0, -100.0 / 3, False],
         [True, 2, 1, -1, -50.0, 4.5, 6.0, 1.5, 100.0 * 1.5 / 4.5, False],
         [None, 2, 2, 0, 0.0, 4.0, 4.0, 0.0, 0.0, True]]),
    # (maker_country, member), NULLs last in both keys:
    #   DE F: T08            fare 3   trips 1 paid 1 riders 1          min 3.0 max 3.0 avg 3
    #   DE T: T03            fare 4   trips 1 paid 1 riders 1          min 4.0 max 4.0 avg 4
    #   NL F: T02 T05        fare 11  trips 2 paid 2 riders {102} = 1  min 6.0 max 6.0 avg 5.5
    #   NL T: T01 T04        fare 3   trips 2 paid 1 riders {101,103}=2 min 1.5 max 2.5 avg 3 / 1 = 3
    #   -- F: T06            fare 2   trips 1 paid 1 riders 0 (only NULL) min 1.0 max 1.0 avg 2
    #   -- T: T07            fare 7   trips 1 paid 1 riders 1          min 8.0 max 8.0 avg 7
    #   total: 30, 8, 7, riders {101,102,103} = 3 (the groups sum to 6), 1.0, 8.0, 30 / 7
    "every_aggregation_by_country_member": (
        ["maker_country", "member"] + ALL_METRICS + ["is_total"],
        [["DE", False, 3.0, 1, 1, 1, 3.0, 3.0, 3.0, False],
         ["DE", True, 4.0, 1, 1, 1, 4.0, 4.0, 4.0, False],
         ["NL", False, 11.0, 2, 2, 1, 6.0, 6.0, 5.5, False],
         ["NL", True, 3.0, 2, 1, 2, 1.5, 2.5, 3.0, False],
         [None, False, 2.0, 1, 1, 0, 1.0, 1.0, 2.0, False],
         [None, True, 7.0, 1, 1, 1, 8.0, 8.0, 7.0, False],
         [None, None, 30.0, 8, 7, 3, 1.0, 8.0, 30.0 / 7, True]]),
    # per trip_date:            plain       electric = true     district = Harbor   NL AND member
    #   05: T01 T02 T03         3+5+4 = 12  T02 5 + T03 4 = 9   T01 3 + T02 5 = 8   T01 3
    #   06: T04 T05 T06         NULL+6+2=8  T05 6               T06 2               T04 fare NULL -> NULL
    #   07: T07 T08             7+3 = 10    T08 3 (T07 NULL)    T07 7               none -> NULL
    "fare_variants_by_day": (
        ["trip_date", "fare_total", "electric_fare", "harbor_fare", "nl_member_fare"],
        [[_D5, 12.0, 9.0, 8.0, 3.0],
         [_D6, 8.0, 6.0, 2.0, None],
         [_D7, 10.0, 3.0, 7.0, None]]),
}


# --- compare with a period missing in some groups (DESIGN.md §6.3 count semantics) ------------
# Shared by the CASE-WHEN fallback tests (test_dialects.py) and the Postgres suite.
# trip_date 05 vs 07 (primary 07), WHERE keeps T01 T02 T03 (05) and T07 T08 (07):
#   DE:   05 T03 (r101, 4)                   07 T08 (r103, 3)
#   NL:   05 T01 (r101, 3), T02 (r102, 5)    07 none
#   NULL: 05 none                            07 T07 (r101, 7; cycle C9 is an orphan)
# count / count_distinct over zero rows are 0 (so NL: "2 trips, then none" -> delta -2);
# sum over zero rows is NULL, and anything minus NULL is NULL.
MISSING_PERIOD_CONTRACT = {
    "metrics": [{"name": "trip_count"}, {"name": "unique_riders"}, {"name": "fare_total"}],
    "group_by": ["maker_country"],
    "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-07"], "primary": "2026-01-07",
                "outputs": ["values", "delta"]},
}
MISSING_PERIOD_EXPECTED = (
    ["maker_country",
     "trip_count_2026-01-05", "trip_count_2026-01-07", "trip_count_delta",
     "unique_riders_2026-01-05", "unique_riders_2026-01-07", "unique_riders_delta",
     "fare_total_2026-01-05", "fare_total_2026-01-07", "fare_total_delta"],
    [["DE", 1, 1, 0, 1, 1, 0, 4.0, 3.0, -1.0],
     ["NL", 2, 0, -2, 2, 0, -2, 8.0, None, None],
     [None, 0, 1, 1, 0, 1, 1, None, 7.0, None]],
)
