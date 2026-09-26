"""S7: compile_contract end to end on DuckDB (DESIGN.md §1, §6.1, §6.6)."""
import copy

import pytest

from walt_compiler import compile_contract
from walt_compiler.errors import UnknownMetric
from tests.conftest import run
from tests.helpers import PDF, assert_result, pdf_contract, pdf_expected, pdf_model


def golden(dialect, name):
    return (PDF / "golden" / dialect / f"{name}.sql").read_text().rstrip("\n")


def test_contract_a_golden_sql():
    assert compile_contract(pdf_model(), pdf_contract("a"), "duckdb") == golden("duckdb", "a")


def test_contract_a_executes(pdf_duckdb):
    cols, rows = run(pdf_duckdb, compile_contract(pdf_model(), pdf_contract("a"), "duckdb"))
    assert_result(cols, rows, pdf_expected("a"))


def test_grouped_distinct_count_executes(pdf_duckdb):
    sql = compile_contract(pdf_model(), {"metrics": [{"name": "order_count"}], "group_by": ["region"]}, "duckdb")
    cols, rows = run(pdf_duckdb, sql)
    assert_result(cols, rows, {"columns": ["region", "order_count"],
                               "rows": [["North", 4], ["South", 3], ["West", 2]]})


# --- join semantics on orphan rows (fixtures/orphan_rows.sql) ---------------------------------

def test_left_join_keeps_facts_whose_dimension_row_is_missing(orphan_duckdb):
    sql = compile_contract(pdf_model(), {"metrics": [{"name": "total_revenue"}], "group_by": ["region"]}, "duckdb")
    cols, rows = run(orphan_duckdb, sql)
    # O-900 (store S9, not in dim_store) survives with region NULL, ordered last.
    assert_result(cols, rows, {"columns": ["region", "total_revenue"],
                               "rows": [["North", 375.0], ["South", 570.0], ["West", 240.0], [None, 40.0]]})


@pytest.mark.parametrize("op, total", [("=", 375.0), ("!=", 810.0)])
def test_dimension_filter_restricts_and_excludes_unknown_values(orphan_duckdb, op, total):
    # A NULL region matches neither = nor != (standard SQL semantics, DESIGN.md §6.1).
    sql = compile_contract(pdf_model(), {"metrics": [{"name": "total_revenue"}],
                                         "filters": [{"field": "region", "op": op, "value": "North"}]}, "duckdb")
    assert run(orphan_duckdb, sql)[1] == [(total,)]


# --- determinism and purity ------------------------------------------------------------------

def test_same_input_same_sql():
    first = compile_contract(pdf_model(), pdf_contract("a"), "duckdb")
    assert all(compile_contract(pdf_model(), pdf_contract("a"), "duckdb") == first for _ in range(100))


def test_inputs_are_not_mutated():
    model, contract = pdf_model(), pdf_contract("a")
    before = copy.deepcopy((model, contract))
    compile_contract(model, contract, "duckdb")
    assert (model, contract) == before


def test_errors_surface_as_compiler_errors():
    with pytest.raises(UnknownMetric):
        compile_contract(pdf_model(), {"metrics": [{"name": "nope"}]}, "duckdb")
