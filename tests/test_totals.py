"""S9: totals -> GROUP BY GROUPING SETS ((g1..gn), ()) + is_total (DESIGN.md §6.4-§6.7).

Every expected number is computed by hand from fixtures/pdf/duckdb_schema.sql (and
fixtures/orphan_rows.sql where noted). PDF fact lines, for reference:

  O-100 2025 S1 North  Online 100      O-200 2026 S1 North  Online 120
  O-101 2025 S3 South  Retail 200      O-201 2026 S2 North  Retail  80
  O-102 2025 S4 West   Online 150      O-202 2026 S3 South  Online 300
                                       O-203 2026 S1 North  Retail  50
                                       O-203 2026 S3 South  Retail  70
                                       O-204 2026 S4 West   Retail  90
Orphans: O-900 2026 S9 (no store -> region NULL) Online 40
         O-901 2026-07-01 (no calendar row -> fiscal_year NULL) S1 North Retail 25
"""
import copy
import os
import subprocess
import sys

import pytest

from walt_compiler import compile_contract
from walt_compiler.aggregations import AGGREGATIONS
from walt_compiler.contract import parse_contract
from walt_compiler.dialects import get_dialect
from walt_compiler.dialects.base import Dialect
from walt_compiler.errors import DuplicateOutputName, UnsupportedFeature
from walt_compiler.joins import JoinStep
from walt_compiler.lower import lower
from walt_compiler.model import build_catalog
from walt_compiler.plan import (ColumnRef, GroupKey, GroupRef, IsTotal, LogicalPlan, Measure, MeasureRef,
                                OrderKey, OutputColumn)
from walt_compiler.resolve import resolve
from tests.conftest import run
from tests.helpers import PDF, ROOT, assert_result, pdf_contract, pdf_expected, pdf_model

COUNT_DISTINCT = AGGREGATIONS["count_distinct"]
REGION = ColumnRef("dim_store", "region", "text")
ORDER_ID = ColumnRef("fact_sales", "order_id", None)
JOIN_STORE = JoinStep("fact_sales", "store_id", "dim_store", "store_id")


def golden(dialect, name):
    return (PDF / "golden" / dialect / f"{name}.sql").read_text().rstrip("\n")


def plan_for(contract, model=None):
    return resolve(parse_contract(contract), build_catalog(model or pdf_model()))


def compile_duck(contract, model=None):
    return compile_contract(model or pdf_model(), contract, "duckdb")


def contract_c(**extra):
    c = copy.deepcopy(pdf_contract("c"))
    c.update(extra)
    return c


# --- acceptance: Contracts B and C on DuckDB -------------------------------------------------

@pytest.mark.parametrize("name", ["b", "c"])
def test_pdf_contract_executes(pdf_duckdb, name):
    # B: Total 450 / 710 / 260 / 57.8 (= 100 * 260 / 450); C: 4 / 3 / 2 + total 8 (O-203 counted once).
    cols, rows = run(pdf_duckdb, compile_duck(pdf_contract(name)))
    assert_result(cols, rows, pdf_expected(name))


@pytest.mark.parametrize("name", ["b", "c"])
def test_pdf_contract_golden_sql(name):
    assert compile_duck(pdf_contract(name)) == golden("duckdb", name)


# --- plan level ------------------------------------------------------------------------------

def test_contract_c_plan():
    assert plan_for(pdf_contract("c")) == LogicalPlan(
        base="fact_sales",
        joins=(JOIN_STORE,),
        group_by=(GroupKey("region", REGION),),
        measures=(Measure("order_count", COUNT_DISTINCT, ORDER_ID, None),),
        where=None,
        grouping_sets=(("region",), ()),
        outputs=(OutputColumn("region", GroupRef("region")),
                 OutputColumn("order_count", MeasureRef("order_count")),
                 OutputColumn("is_total", IsTotal())),
        order_by=(OrderKey("is_total"), OrderKey("region")),
    )


def test_contract_b_plan_puts_is_total_after_every_metric_column():
    plan = plan_for(pdf_contract("b"))
    assert plan.grouping_sets == (("region",), ())
    assert [o.name for o in plan.outputs] == ["region", "total_revenue_2025", "total_revenue_2026",
                                              "total_revenue_delta", "total_revenue_pct_change", "is_total"]
    assert plan.outputs[-1] == OutputColumn("is_total", IsTotal())
    assert plan.order_by == (OrderKey("is_total"), OrderKey("region"))
    assert "is_total" not in [m.alias for m in plan.measures]      # is_total is not an aggregate


def test_no_is_total_without_totals():
    c = contract_c()
    del c["totals"]
    plan = plan_for(c)
    assert plan.grouping_sets is None
    assert [o.name for o in plan.outputs] == ["region", "order_count"]
    assert plan.order_by == (OrderKey("region"),)


# --- orphan rows: a NULL group value is not the total ----------------------------------------

def test_null_region_row_is_distinct_from_the_total_row(orphan_duckdb):
    # Distinct order_ids per region (C on PDF + orphans):
    #   North (S1, S2): O-100, O-200, O-201, O-203, O-901 = 5
    #   South (S3):     O-101, O-202, O-203               = 3
    #   West  (S4):     O-102, O-204                      = 2
    #   NULL  (S9):     O-900                             = 1
    #   total: O-100 101 102 200 201 202 203 204 900 901  = 10 (not 5+3+2+1 = 11: O-203 once)
    cols, rows = run(orphan_duckdb, compile_duck(pdf_contract("c")))
    assert_result(cols, rows, {"columns": ["region", "order_count", "is_total"],
                               "rows": [["North", 5, False], ["South", 3, False], ["West", 2, False],
                                        [None, 1, False],
                                        [None, 10, True]]})


def test_dimension_filter_excludes_the_orphans_from_groups_and_total(orphan_duckdb):
    # region != 'North' drops North and the NULL region (O-900): NULL matches no comparison.
    #   South 3, West 2; total distinct over S3+S4 lines: O-101, O-202, O-203, O-102, O-204 = 5
    c = contract_c(filters=[{"field": "region", "op": "!=", "value": "North"}])
    cols, rows = run(orphan_duckdb, compile_duck(c))
    assert_result(cols, rows, {"columns": ["region", "order_count", "is_total"],
                               "rows": [["South", 3, False], ["West", 2, False], [None, 5, True]]})


def test_filter_on_another_dimension_keeps_null_region_but_drops_null_date(orphan_duckdb):
    # fiscal_year = 2026: O-901 (fiscal_year NULL) is excluded, O-900 (region NULL, 2026-02-01) is kept.
    #   North: 120 + 80 + 50 = 250; South: 300 + 70 = 370; West: 90; NULL: 40
    #   total: 250 + 370 + 90 + 40 = 750
    c = {"metrics": [{"name": "total_revenue"}], "group_by": ["region"], "totals": "grand",
         "filters": [{"field": "fiscal_year", "op": "=", "value": 2026}]}
    cols, rows = run(orphan_duckdb, compile_duck(c))
    assert_result(cols, rows, {"columns": ["region", "total_revenue", "is_total"],
                               "rows": [["North", 250.0, False], ["South", 370.0, False], ["West", 90.0, False],
                                        [None, 40.0, False],
                                        [None, 750.0, True]]})


# --- totals recompute every aggregate against the base rows ----------------------------------

def avg_model():
    m = copy.deepcopy(pdf_model())
    m["metrics"].append({"name": "avg_revenue", "agg": "avg", "expression": "revenue",
                         "model": "fact_sales", "measure_class": "non_additive"})
    return m


def test_average_total_is_over_base_rows_not_an_average_of_averages(pdf_duckdb):
    # North: (100 + 120 + 80 + 50) / 4 = 87.5;  South: (200 + 300 + 70) / 3 = 190;  West: (150 + 90) / 2 = 120
    # total over all 9 lines: 1160 / 9 = 128.888...
    # (an average of the group averages would be (87.5 + 190 + 120) / 3 = 132.5: wrong)
    c = {"metrics": [{"name": "avg_revenue"}], "group_by": ["region"], "totals": "grand"}
    cols, rows = run(pdf_duckdb, compile_duck(c, avg_model()))
    assert_result(cols, rows, {"columns": ["region", "avg_revenue", "is_total"],
                               "rows": [["North", 87.5, False], ["South", 190.0, False], ["West", 120.0, False],
                                        [None, 1160.0 / 9, True]]})
    assert rows[-1][1] != pytest.approx(132.5)


# --- several group_by columns: still exactly one grand-total row -----------------------------

def two_group_contract():
    return {"metrics": [{"name": "total_revenue"}], "group_by": ["region", "channel"], "totals": "grand"}


def test_multiple_group_by_plan():
    plan = plan_for(two_group_contract())
    assert plan.grouping_sets == (("region", "channel"), ())
    assert [o.name for o in plan.outputs] == ["region", "channel", "total_revenue", "is_total"]
    assert plan.order_by == (OrderKey("is_total"), OrderKey("region"), OrderKey("channel"))


def test_multiple_group_by_sql_uses_one_full_set_and_the_empty_set():
    sql = compile_duck(two_group_contract())
    assert 'GROUP BY GROUPING SETS (("dim_store"."region", "fact_sales"."channel"), ())' in sql
    assert 'GROUPING("dim_store"."region") = 1 AS "is_total"' in sql
    assert sql.endswith('ORDER BY "is_total" ASC, "region" ASC NULLS LAST, "channel" ASC NULLS LAST')


def test_multiple_group_by_executes_with_a_single_total_row_last(pdf_duckdb):
    # North Online 100 + 120 = 220, North Retail 80 + 50 = 130, South Online 300,
    # South Retail 200 + 70 = 270, West Online 150, West Retail 90; total 1160.
    cols, rows = run(pdf_duckdb, compile_duck(two_group_contract()))
    assert_result(cols, rows, {"columns": ["region", "channel", "total_revenue", "is_total"],
                               "rows": [["North", "Online", 220.0, False], ["North", "Retail", 130.0, False],
                                        ["South", "Online", 300.0, False], ["South", "Retail", 270.0, False],
                                        ["West", "Online", 150.0, False], ["West", "Retail", 90.0, False],
                                        [None, None, 1160.0, True]]})
    assert [r[-1] for r in rows].count(True) == 1


# --- compare + totals ------------------------------------------------------------------------

def test_compare_totals_with_a_second_metric(pdf_duckdb):
    # order_count (distinct order_id) by region and fiscal year:
    #   North 2025: O-100 = 1;  2026: O-200, O-201, O-203 = 3  -> delta 2, pct 100 * 2 / 1 = 200
    #   South 2025: O-101 = 1;  2026: O-202, O-203 = 2         -> delta 1, pct 100
    #   West  2025: O-102 = 1;  2026: O-204 = 1                -> delta 0, pct 0
    #   total 2025: 3;  2026: O-200..O-204 = 5 (not 3+2+1 = 6) -> delta 2, pct 100 * 2 / 3
    c = copy.deepcopy(pdf_contract("b"))
    c["metrics"].append({"name": "order_count"})
    cols, rows = run(pdf_duckdb, compile_duck(c))
    assert_result(cols, rows, {
        "columns": ["region", "total_revenue_2025", "total_revenue_2026", "total_revenue_delta",
                    "total_revenue_pct_change", "order_count_2025", "order_count_2026", "order_count_delta",
                    "order_count_pct_change", "is_total"],
        "rows": [["North", 100.0, 250.0, 150.0, 150.0, 1, 3, 2, 200.0, False],
                 ["South", 200.0, 370.0, 170.0, 85.0, 1, 2, 1, 100.0, False],
                 ["West", 150.0, 90.0, -60.0, -40.0, 1, 1, 0, 0.0, False],
                 [None, 450.0, 710.0, 260.0, 57.77777777777778, 3, 5, 2, 200.0 / 3, True]]})


def test_compare_totals_with_a_per_metric_filter(pdf_duckdb):
    # Online lines: O-100 North 2025 100, O-102 West 2025 150, O-200 North 2026 120, O-202 South 2026 300.
    #   North: 100 -> 120, delta 20, pct 20
    #   South: no Online row in 2025 -> NULL; 2026 300; delta and pct NULL
    #   West:  150; no Online row in 2026 -> NULL; delta and pct NULL
    #   total: 250 -> 420, delta 170, pct 100 * 170 / 250 = 68
    c = {"metrics": [{"name": "total_revenue", "as": "online_rev",
                      "filters": [{"field": "channel", "op": "=", "value": "Online"}]}],
         "group_by": ["region"],
         "compare": {"dimension": "fiscal_year", "periods": ["2025", "2026"], "primary": "2026",
                     "outputs": ["values", "delta", "pct_change"]},
         "totals": "grand"}
    cols, rows = run(pdf_duckdb, compile_duck(c))
    assert_result(cols, rows, {
        "columns": ["region", "online_rev_2025", "online_rev_2026", "online_rev_delta",
                    "online_rev_pct_change", "is_total"],
        "rows": [["North", 100.0, 120.0, 20.0, 20.0, False],
                 ["South", None, 300.0, None, None, False],
                 ["West", 150.0, None, None, None, False],
                 [None, 250.0, 420.0, 170.0, 68.0, True]]})


# --- is_total name collisions ----------------------------------------------------------------

def test_metric_alias_is_total_clashes_with_totals():
    # Names are checked in output order (groups, metrics, is_total): the later duplicate is is_total,
    # which exists only because of the contract's "totals" key.
    c = contract_c(metrics=[{"name": "order_count", "as": "is_total"}])
    with pytest.raises(DuplicateOutputName) as exc:
        plan_for(c)
    assert exc.value.path == "/totals"


def test_group_by_dimension_named_is_total_clashes_with_totals():
    m = copy.deepcopy(pdf_model())
    m["dimensions"].append({"name": "is_total", "model": "fact_sales", "type": "text", "column": "channel"})
    c = contract_c(group_by=["is_total"])
    with pytest.raises(DuplicateOutputName) as exc:
        plan_for(c, m)
    assert exc.value.path == "/totals"


def test_is_total_is_a_free_name_without_totals():
    # is_total is only generated when totals are requested (DESIGN.md §6.4).
    c = contract_c(metrics=[{"name": "order_count", "as": "is_total"}])
    del c["totals"]
    assert [o.name for o in plan_for(c).outputs] == ["region", "is_total"]


# --- dialect capability: no GROUPING SETS -> refuse, never emit SQL --------------------------

class NoGroupingSetsDialect(Dialect):
    """Test-only dialect without GROUPING SETS support."""
    name = "no_grouping_sets"
    supports_grouping_sets = False


@pytest.mark.parametrize("name", ["b", "c"])
def test_dialect_without_grouping_sets_refuses_totals(name):
    plan = plan_for(pdf_contract(name))         # resolving is dialect-free and must succeed
    select = lower(plan)
    assert get_dialect("duckdb").render(select) == golden("duckdb", name)
    with pytest.raises(UnsupportedFeature):
        NoGroupingSetsDialect().render(select)


def test_dialect_without_grouping_sets_still_renders_plain_group_by():
    c = contract_c()
    del c["totals"]
    assert "GROUP BY" in NoGroupingSetsDialect().render(lower(plan_for(c)))


# --- determinism across processes ------------------------------------------------------------

_SCRIPT = """
import json
from walt_compiler import compile_contract
from tests.helpers import pdf_contract, pdf_model
print(json.dumps([compile_contract(pdf_model(), pdf_contract(n), "duckdb") for n in ("b", "c")]))
"""


def test_sql_is_identical_across_hash_seeds():
    outputs = []
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0].strip() != ""
