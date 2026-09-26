"""S6: resolve (semantic analysis) -> LogicalPlan, base features (DESIGN.md §6.1, §6.2, §7).

Plan-level assertions only: no SQL is involved. compare / totals are added in S8 / S9.
"""
import copy

import pytest

from walt_compiler.aggregations import AGGREGATIONS
from walt_compiler.contract import parse_contract
from walt_compiler.errors import (DuplicateOutputName, InvalidLiteral, ModelMismatch, MultipleFactTables,
                                  NoJoinPath, UnknownDimension, UnknownMetric, UnsupportedOperator)
from walt_compiler.joins import JoinStep
from walt_compiler.model import build_catalog
from walt_compiler.options import CompileOptions
from walt_compiler.plan import (And, ColumnRef, Compare, GroupKey, GroupRef, InList, LogicalPlan, Measure,
                                MeasureRef, OrderKey, OutputColumn)
from walt_compiler.resolve import resolve
from walt_compiler.types import TypedLiteral
from tests.helpers import pdf_contract, pdf_model

SUM, COUNT_DISTINCT = AGGREGATIONS["sum"], AGGREGATIONS["count_distinct"]
REVENUE = ColumnRef("fact_sales", "revenue", None)          # metric columns have no declared type
CHANNEL = ColumnRef("fact_sales", "channel", "text")
REGION = ColumnRef("dim_store", "region", "text")
FISCAL_YEAR = ColumnRef("dim_calendar", "fiscal_year", "integer")
JOIN_STORE = JoinStep("fact_sales", "store_id", "dim_store", "store_id")
JOIN_CALENDAR = JoinStep("fact_sales", "order_date", "dim_calendar", "date")


def extended_model():
    """PDF model + a boolean dimension, a second fact dataset and an unreachable dataset."""
    m = copy.deepcopy(pdf_model())
    m["datasets"] += [{"name": "fact_returns"}, {"name": "dim_island"}]
    m["metrics"].append({"name": "refund_total", "agg": "sum", "expression": "refund", "model": "fact_returns"})
    m["dimensions"] += [{"name": "is_promo", "model": "fact_sales", "type": "boolean"},
                        {"name": "island_attr", "model": "dim_island", "type": "text"}]
    return m


def plan_for(contract, model=None, options=CompileOptions()):
    return resolve(parse_contract(contract), build_catalog(model or pdf_model()), options)


def fails(contract, error, model=None, options=CompileOptions()):
    with pytest.raises(error) as exc:
        plan_for(contract, model or extended_model(), options)
    return exc.value


# --- happy path ------------------------------------------------------------------------------

def test_contract_a_plan():
    assert plan_for(pdf_contract("a")) == LogicalPlan(
        base="fact_sales",
        joins=(JOIN_CALENDAR,),     # channel lives on the fact itself: no dim join for it
        group_by=(),
        measures=(
            Measure("online_rev", SUM, REVENUE, Compare(CHANNEL, "=", TypedLiteral("Online", "text"))),
            Measure("total_revenue", SUM, REVENUE, None),
        ),
        where=Compare(FISCAL_YEAR, "=", TypedLiteral(2026, "integer")),
        grouping_sets=None,
        outputs=(OutputColumn("online_rev", MeasureRef("online_rev")),
                 OutputColumn("total_revenue", MeasureRef("total_revenue"))),
        order_by=(),
    )


def test_grouped_plan():
    assert plan_for({"metrics": [{"name": "order_count"}], "group_by": ["region"]}) == LogicalPlan(
        base="fact_sales",
        joins=(JOIN_STORE,),
        group_by=(GroupKey("region", REGION),),
        measures=(Measure("order_count", COUNT_DISTINCT, ColumnRef("fact_sales", "order_id", None), None),),
        where=None,
        grouping_sets=None,
        outputs=(OutputColumn("region", GroupRef("region")), OutputColumn("order_count", MeasureRef("order_count"))),
        order_by=(OrderKey("region"),),
    )


def test_group_by_and_outputs_follow_contract_order():
    plan = plan_for({"metrics": [{"name": "total_revenue"}, {"name": "order_count"}],
                     "group_by": ["channel", "region"]})
    assert [o.name for o in plan.outputs] == ["channel", "region", "total_revenue", "order_count"]
    assert plan.order_by == (OrderKey("channel"), OrderKey("region"))


def test_several_per_metric_filters_are_anded():
    plan = plan_for({"metrics": [{"name": "total_revenue", "filters": [
        {"field": "channel", "op": "=", "value": "Online"},
        {"field": "region", "op": "!=", "value": "West"}]}]})
    assert plan.measures[0].condition == And((
        Compare(CHANNEL, "=", TypedLiteral("Online", "text")),
        Compare(REGION, "!=", TypedLiteral("West", "text"))))


def test_per_metric_filter_on_another_dataset_adds_its_join():
    plan = plan_for({"metrics": [{"name": "total_revenue",
                                  "filters": [{"field": "region", "op": "=", "value": "North"}]}]})
    assert plan.joins == (JOIN_STORE,)
    assert plan.where is None


def test_several_contract_filters_are_anded_and_joins_are_deduplicated():
    plan = plan_for({"metrics": [{"name": "total_revenue"}], "group_by": ["region"],
                     "filters": [{"field": "region", "op": "in", "value": ["North", "South"]},
                                 {"field": "fiscal_year", "op": ">=", "value": "2025"}]})
    assert plan.where == And((
        InList(REGION, (TypedLiteral("North", "text"), TypedLiteral("South", "text"))),
        Compare(FISCAL_YEAR, ">=", TypedLiteral(2025, "integer"))))
    assert plan.joins == (JOIN_STORE, JOIN_CALENDAR)


def test_in_list_literals_are_coerced():
    plan = plan_for({"metrics": [{"name": "total_revenue"}],
                     "filters": [{"field": "fiscal_year", "op": "in", "value": ["2025", 2026]}]})
    assert plan.where == InList(FISCAL_YEAR, (TypedLiteral(2025, "integer"), TypedLiteral(2026, "integer")))


def test_filter_model_is_optional():
    plan = plan_for({"metrics": [{"name": "total_revenue"}],
                     "filters": [{"field": "fiscal_year", "op": "=", "value": 2026}]})
    assert plan.where == Compare(FISCAL_YEAR, "=", TypedLiteral(2026, "integer"))


def test_only_fact_columns_means_no_joins():
    assert plan_for({"metrics": [{"name": "total_revenue"}], "group_by": ["channel"]}).joins == ()


# --- semantic errors -------------------------------------------------------------------------

def test_unknown_metric_is_suggested():
    err = fails({"metrics": [{"name": "total_revenu"}]}, UnknownMetric)
    assert err.path == "/metrics/0/name"
    assert err.suggestions == ("total_revenue",)


def test_suggestions_respect_options():
    err = fails({"metrics": [{"name": "total_revenu"}]}, UnknownMetric, options=CompileOptions(max_suggestions=0))
    assert err.suggestions == ()


@pytest.mark.parametrize("contract, path", [
    ({"metrics": [{"name": "total_revenue"}], "group_by": ["regon"]}, "/group_by/0"),
    ({"metrics": [{"name": "total_revenue"}], "filters": [{"field": "regon", "op": "=", "value": "x"}]},
     "/filters/0/field"),
    ({"metrics": [{"name": "total_revenue", "filters": [{"field": "regon", "op": "=", "value": "x"}]}]},
     "/metrics/0/filters/0/field"),
])
def test_unknown_dimension_is_suggested(contract, path):
    err = fails(contract, UnknownDimension)
    assert err.path == path
    assert err.suggestions == ("region",)


def test_filter_model_must_match_the_declared_model():
    err = fails({"metrics": [{"name": "total_revenue"}],
                 "filters": [{"field": "fiscal_year", "op": "=", "value": 2026, "model": "fact_sales"}]},
                ModelMismatch)
    assert err.path == "/filters/0/model"
    assert "dim_calendar" in err.message


@pytest.mark.parametrize("value, path", [
    ("twenty", "/filters/0/value"),
    (None, "/filters/0/value"),
])
def test_invalid_literal(value, path):
    err = fails({"metrics": [{"name": "total_revenue"}],
                 "filters": [{"field": "fiscal_year", "op": "=", "value": value}]}, InvalidLiteral)
    assert err.path == path


def test_invalid_literal_inside_in_list():
    err = fails({"metrics": [{"name": "total_revenue"}],
                 "filters": [{"field": "fiscal_year", "op": "in", "value": [2025, "x"]}]}, InvalidLiteral)
    assert err.path == "/filters/0/value/1"


def test_ordering_operator_on_boolean_dimension():
    err = fails({"metrics": [{"name": "total_revenue"}],
                 "filters": [{"field": "is_promo", "op": "<", "value": True}]}, UnsupportedOperator)
    assert err.path == "/filters/0/op"
    assert "boolean" in err.message


def test_metrics_from_different_datasets():
    err = fails({"metrics": [{"name": "total_revenue"}, {"name": "refund_total"}]}, MultipleFactTables)
    assert err.path == "/metrics/1/name"


def test_same_metric_twice_without_alias_is_a_duplicate_output():
    err = fails({"metrics": [{"name": "total_revenue"}, {"name": "total_revenue"}]}, DuplicateOutputName)
    assert err.path == "/metrics/1/name"


def test_alias_clashing_with_group_column_is_a_duplicate_output():
    err = fails({"metrics": [{"name": "total_revenue", "as": "region"}], "group_by": ["region"]},
                DuplicateOutputName)
    assert err.path == "/metrics/0/as"


def test_join_errors_point_at_the_contract_reference():
    err = fails({"metrics": [{"name": "total_revenue"}], "group_by": ["channel", "island_attr"]}, NoJoinPath)
    assert err.path == "/group_by/1"
