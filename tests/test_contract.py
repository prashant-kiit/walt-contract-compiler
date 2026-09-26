"""S4: contract JSON -> typed Contract tree (DESIGN.md §6, syntactic analysis).

Parsing never looks at the semantic model: names, types and literal values are checked in resolve.
"""
import copy
import dataclasses

import pytest

from walt_compiler.contract import CompareSpec, Contract, FilterSpec, MetricRequest, parse_contract
from walt_compiler.errors import InvalidContract, UnsupportedFeature, UnsupportedOperator
from tests.helpers import pdf_contract


def fails(doc, error=InvalidContract):
    with pytest.raises(error) as exc:
        parse_contract(doc)
    return exc.value


def with_(name, mutate):
    doc = copy.deepcopy(pdf_contract(name))
    mutate(doc)
    return doc


# --- the PDF contracts ------------------------------------------------------------------------

def test_contract_a():
    assert parse_contract(pdf_contract("a")) == Contract(
        metrics=(
            MetricRequest("total_revenue", alias="online_rev",
                          filters=(FilterSpec("channel", "=", "Online", "fact_sales", path="/metrics/0/filters/0"),),
                          path="/metrics/0"),
            MetricRequest("total_revenue", alias=None, filters=(), path="/metrics/1"),
        ),
        group_by=(),
        filters=(FilterSpec("fiscal_year", "=", 2026, "dim_calendar", path="/filters/0"),),
        compare=None,
        totals=None,
    )


def test_contract_b():
    c = parse_contract(pdf_contract("b"))
    assert c.metrics == (MetricRequest("total_revenue", None, (), "/metrics/0"),)
    assert c.group_by == ("region",)
    assert c.filters == ()
    assert c.compare == CompareSpec("fiscal_year", periods=("2025", "2026"), primary="2026",
                                    outputs=("values", "delta", "pct_change"), path="/compare")
    assert c.totals == "grand"


def test_contract_c():
    c = parse_contract(pdf_contract("c"))
    assert (c.metrics[0].name, c.group_by, c.compare, c.totals) == ("order_count", ("region",), None, "grand")


def test_minimal_contract_gets_defaults():
    assert parse_contract({"metrics": [{"name": "m"}]}) == Contract(
        metrics=(MetricRequest("m", None, (), "/metrics/0"),), group_by=(), filters=(), compare=None, totals=None)


def test_in_list_is_stored_as_tuple_and_filter_model_is_optional():
    c = parse_contract({"metrics": [{"name": "m"}],
                        "filters": [{"field": "region", "op": "in", "value": ["North", "South"]}]})
    assert c.filters == (FilterSpec("region", "in", ("North", "South"), None, "/filters/0"),)


def test_tree_is_immutable():
    c = parse_contract(pdf_contract("a"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.totals = "grand"


# --- shape errors (InvalidContract) ------------------------------------------------------------

@pytest.mark.parametrize("doc, path", [
    ([], ""),
    ({}, "/metrics"),
    ({"metrics": []}, "/metrics"),
    ({"metrics": [{}]}, "/metrics/0/name"),
    ({"metrics": [{"name": "m", "as": ""}]}, "/metrics/0/as"),
    ({"metrics": [{"name": "m", "alias": "x"}]}, "/metrics/0/alias"),
    ({"metrics": [{"name": "m"}], "group_by": "region"}, "/group_by"),
    ({"metrics": [{"name": "m"}], "group_by": ["region", "region"]}, "/group_by/1"),
    ({"metrics": [{"name": "m"}], "filters": [{"field": "f", "op": "="}]}, "/filters/0/value"),
    ({"metrics": [{"name": "m"}], "filters": [{"field": "f", "op": "=", "value": 1, "model": 3}]}, "/filters/0/model"),
])
def test_shape_errors(doc, path):
    assert fails(doc).path == path


def test_unknown_top_level_key_is_suggested():
    err = fails({"metrics": [{"name": "m"}], "group-by": ["region"]})
    assert err.path == "/group-by"
    assert err.suggestions == ("group_by",)


# --- operators and arity -----------------------------------------------------------------------

def test_unknown_operator():
    err = fails({"metrics": [{"name": "m"}], "filters": [{"field": "f", "op": "==", "value": 1}]},
                error=UnsupportedOperator)
    assert err.path == "/filters/0/op"


def test_unknown_operator_is_suggested():
    err = fails({"metrics": [{"name": "m"}], "filters": [{"field": "f", "op": "inn", "value": [1]}]},
                error=UnsupportedOperator)
    assert err.suggestions == ("in",)


def test_per_metric_filter_errors_point_inside_the_metric():
    err = fails(with_("a", lambda d: d["metrics"][0]["filters"][0].update(op="like")), error=UnsupportedOperator)
    assert err.path == "/metrics/0/filters/0/op"


@pytest.mark.parametrize("op, value, path", [
    ("in", "North", "/filters/0/value"),          # `in` needs a list
    ("in", [], "/filters/0/value"),               # ... a non-empty one
    ("in", ["North", ["South"]], "/filters/0/value/1"),  # ... of scalars
    ("in", ["North", {"x": 1}], "/filters/0/value/1"),
    ("=", ["North"], "/filters/0/value"),         # scalar operators need a scalar
    ("<", {"x": 1}, "/filters/0/value"),
])
def test_operator_arity(op, value, path):
    err = fails({"metrics": [{"name": "m"}], "filters": [{"field": "f", "op": op, "value": value}]})
    assert err.path == path


# --- totals ----------------------------------------------------------------------------------

def test_unsupported_totals_value():
    err = fails(with_("c", lambda d: d.update(totals="subtotals")), error=UnsupportedFeature)
    assert err.path == "/totals"


def test_totals_typo_is_suggested():
    err = fails(with_("c", lambda d: d.update(totals="grnd")), error=UnsupportedFeature)
    assert err.suggestions == ("grand",)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(group_by=[]),
    lambda d: d.pop("group_by"),
])
def test_totals_without_group_by(mutate):
    assert fails(with_("c", mutate)).path == "/totals"


# --- compare shape (counts / primary membership are semantic: checked in resolve) --------------

@pytest.mark.parametrize("mutate, path", [
    (lambda d: d["compare"].pop("primary"), "/compare/primary"),
    (lambda d: d["compare"].update(outputs=[]), "/compare/outputs"),
    (lambda d: d["compare"].update(outputs=["values", "values"]), "/compare/outputs/1"),
    (lambda d: d["compare"].update(outputs=["values", "percent"]), "/compare/outputs/1"),
    (lambda d: d["compare"].update(periods=["2025", {"y": 2026}]), "/compare/periods/1"),
    (lambda d: d["compare"].update(primary=["2026"]), "/compare/primary"),
    (lambda d: d["compare"].update(dimension=""), "/compare/dimension"),
])
def test_compare_shape_errors(mutate, path):
    assert fails(with_("b", mutate)).path == path


def test_compare_output_typo_is_suggested():
    err = fails(with_("b", lambda d: d["compare"].update(outputs=["values", "pct_chnge"])))
    assert err.suggestions == ("pct_change",)
