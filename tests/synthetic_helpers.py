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
