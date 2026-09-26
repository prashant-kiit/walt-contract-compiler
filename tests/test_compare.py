"""S8: compare -> per-period conditional measures + derived outputs (DESIGN.md §6.3, §6.7).

Totals are added in S9, so these contracts omit "totals".
"""
import copy

import pytest

from walt_compiler import compile_contract
from walt_compiler.aggregations import AGGREGATIONS
from walt_compiler.contract import parse_contract
from walt_compiler.errors import (ConflictingFilter, DuplicateOutputName, InvalidCompare, InvalidLiteral,
                                  UnknownDimension)
from walt_compiler.model import build_catalog
from walt_compiler.plan import (And, ColumnRef, Compare, GroupRef, InList, Measure, MeasureRef, OutputColumn,
                                PctChange, Sub)
from walt_compiler.resolve import resolve
from walt_compiler.types import TypedLiteral
from tests.conftest import run
from tests.helpers import assert_result, pdf_contract, pdf_model

SUM = AGGREGATIONS["sum"]
REVENUE = ColumnRef("fact_sales", "revenue", None)
FY = ColumnRef("dim_calendar", "fiscal_year", "integer")
CHANNEL = ColumnRef("fact_sales", "channel", "text")
Y2025, Y2026 = TypedLiteral(2025, "integer"), TypedLiteral(2026, "integer")


def contract_b_without_totals(**compare):
    c = copy.deepcopy(pdf_contract("b"))
    del c["totals"]
    c["compare"].update(compare)
    return c


def plan_for(contract, model=None):
    return resolve(parse_contract(contract), build_catalog(model or pdf_model()))


def fails(contract, error, model=None):
    with pytest.raises(error) as exc:
        plan_for(contract, model)
    return exc.value


# --- expansion -------------------------------------------------------------------------------

def test_compare_expands_into_period_measures_and_derived_outputs():
    plan = plan_for(contract_b_without_totals())
    assert plan.measures == (
        Measure("total_revenue_2025", SUM, REVENUE, Compare(FY, "=", Y2025)),
        Measure("total_revenue_2026", SUM, REVENUE, Compare(FY, "=", Y2026)),
    )
    assert plan.where == InList(FY, (Y2025, Y2026))
    c25, c26 = MeasureRef("total_revenue_2025"), MeasureRef("total_revenue_2026")
    assert plan.outputs == (
        OutputColumn("region", GroupRef("region")),
        OutputColumn("total_revenue_2025", c25),
        OutputColumn("total_revenue_2026", c26),
        OutputColumn("total_revenue_delta", Sub(c26, c25)),
        OutputColumn("total_revenue_pct_change", PctChange(c26, c25)),
    )


def test_contract_b_without_totals_executes(pdf_duckdb):
    cols, rows = run(pdf_duckdb, compile_contract(pdf_model(), contract_b_without_totals(), "duckdb"))
    assert_result(cols, rows, {
        "columns": ["region", "total_revenue_2025", "total_revenue_2026",
                    "total_revenue_delta", "total_revenue_pct_change"],
        "rows": [["North", 100.0, 250.0, 150.0, 150.0],
                 ["South", 200.0, 370.0, 170.0, 85.0],
                 ["West", 150.0, 90.0, -60.0, -40.0]]})


def test_primary_decides_the_direction_of_delta():
    plan = plan_for(contract_b_without_totals(primary="2025"))
    c25, c26 = MeasureRef("total_revenue_2025"), MeasureRef("total_revenue_2026")
    assert plan.outputs[3:] == (OutputColumn("total_revenue_delta", Sub(c25, c26)),
                                OutputColumn("total_revenue_pct_change", PctChange(c25, c26)))


def test_output_order_is_fixed_regardless_of_contract_order():
    plan = plan_for(contract_b_without_totals(outputs=["pct_change", "values"]))
    assert [o.name for o in plan.outputs] == ["region", "total_revenue_2025", "total_revenue_2026",
                                              "total_revenue_pct_change"]


def test_only_requested_outputs_are_projected_but_both_periods_are_measured():
    plan = plan_for(contract_b_without_totals(outputs=["delta"]))
    assert [o.name for o in plan.outputs] == ["region", "total_revenue_delta"]
    assert [m.alias for m in plan.measures] == ["total_revenue_2025", "total_revenue_2026"]


def test_several_metrics_keep_their_columns_together():
    c = contract_b_without_totals(outputs=["values", "delta"])
    c["metrics"].append({"name": "order_count"})
    assert [o.name for o in plan_for(c).outputs] == [
        "region", "total_revenue_2025", "total_revenue_2026", "total_revenue_delta",
        "order_count_2025", "order_count_2026", "order_count_delta"]


def test_periods_given_as_numbers():
    plan = plan_for(contract_b_without_totals(periods=[2025, 2026], primary=2026))
    assert [m.alias for m in plan.measures] == ["total_revenue_2025", "total_revenue_2026"]


def test_compare_with_per_metric_filter_and_alias(pdf_duckdb):
    c = {"metrics": [{"name": "total_revenue", "as": "online_rev",
                      "filters": [{"field": "channel", "op": "=", "value": "Online"}]}],
         "compare": {"dimension": "fiscal_year", "periods": ["2025", "2026"], "primary": "2026",
                     "outputs": ["values", "delta", "pct_change"]}}
    plan = plan_for(c)
    online = Compare(CHANNEL, "=", TypedLiteral("Online", "text"))
    assert plan.measures[0] == Measure("online_rev_2025", SUM, REVENUE, And((online, Compare(FY, "=", Y2025))))
    cols, rows = run(pdf_duckdb, compile_contract(pdf_model(), c, "duckdb"))
    assert_result(cols, rows, {
        "columns": ["online_rev_2025", "online_rev_2026", "online_rev_delta", "online_rev_pct_change"],
        "rows": [[250.0, 420.0, 170.0, 68.0]]})


def test_compare_works_on_any_dimension_and_uses_period_labels_verbatim(pdf_duckdb):
    c = {"metrics": [{"name": "total_revenue"}],
         "compare": {"dimension": "channel", "periods": ["Online", "Retail"], "primary": "Online",
                     "outputs": ["values", "delta", "pct_change"]}}
    cols, rows = run(pdf_duckdb, compile_contract(pdf_model(), c, "duckdb"))
    assert_result(cols, rows, {
        "columns": ["total_revenue_Online", "total_revenue_Retail", "total_revenue_delta",
                    "total_revenue_pct_change"],
        "rows": [[670.0, 490.0, 180.0, 100.0 * 180.0 / 490.0]]})


# --- errors ----------------------------------------------------------------------------------

def test_unknown_compare_dimension():
    err = fails(contract_b_without_totals(dimension="fiscal_yr"), UnknownDimension)
    assert err.path == "/compare/dimension"
    assert err.suggestions == ("fiscal_year",)


def test_compare_dimension_in_group_by():
    c = contract_b_without_totals()
    c["group_by"] = ["region", "fiscal_year"]
    assert fails(c, InvalidCompare).path == "/compare/dimension"


@pytest.mark.parametrize("periods", [["2025"], ["2024", "2025", "2026"], []])
def test_exactly_two_periods(periods):
    assert fails(contract_b_without_totals(periods=periods), InvalidCompare).path == "/compare/periods"


def test_periods_must_differ_after_coercion():
    err = fails(contract_b_without_totals(periods=["2025", 2025], primary=2025), InvalidCompare)
    assert err.path == "/compare/periods/1"


def test_primary_must_be_one_of_the_periods():
    assert fails(contract_b_without_totals(primary="2024"), InvalidCompare).path == "/compare/primary"


@pytest.mark.parametrize("compare, path", [
    ({"periods": ["last year", "2026"]}, "/compare/periods/0"),
    ({"primary": "this year"}, "/compare/primary"),
])
def test_periods_are_coerced_to_the_dimension_type(compare, path):
    assert fails(contract_b_without_totals(**compare), InvalidLiteral).path == path


def test_contract_filter_on_the_compare_dimension_conflicts():
    c = contract_b_without_totals()
    c["filters"] = [{"field": "region", "op": "=", "value": "North"},
                    {"field": "fiscal_year", "op": "=", "value": 2026}]
    assert fails(c, ConflictingFilter).path == "/filters/1/field"


def test_per_metric_filter_on_the_compare_dimension_conflicts():
    c = contract_b_without_totals()
    c["metrics"][0]["filters"] = [{"field": "fiscal_year", "op": ">", "value": 2020}]
    assert fails(c, ConflictingFilter).path == "/metrics/0/filters/0/field"


def test_generated_names_are_checked_for_duplicates():
    m = copy.deepcopy(pdf_model())
    m["dimensions"].append({"name": "total_revenue_delta", "model": "fact_sales", "type": "text", "column": "channel"})
    c = contract_b_without_totals()
    c["group_by"] = ["total_revenue_delta"]
    assert fails(c, DuplicateOutputName, model=m).path == "/metrics/0/name"
