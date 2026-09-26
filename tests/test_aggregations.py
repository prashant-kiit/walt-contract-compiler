"""S2: aggregation registry (DESIGN.md §5)."""
import pytest

from walt_compiler.aggregations import AGGREGATIONS


def test_registry_holds_exactly_the_designed_aggregations():
    assert sorted(AGGREGATIONS) == ["avg", "count", "count_distinct", "max", "min", "sum"]


def test_registry_is_read_only():
    with pytest.raises(TypeError):
        AGGREGATIONS["median"] = object()


@pytest.mark.parametrize("name, function, distinct", [
    ("sum", "SUM", False),
    ("count", "COUNT", False),
    ("count_distinct", "COUNT", True),
    ("min", "MIN", False),
    ("max", "MAX", False),
    ("avg", "AVG", False),
])
def test_sql_function_and_distinct(name, function, distinct):
    spec = AGGREGATIONS[name]
    assert (spec.name, spec.function, spec.distinct) == (name, function, distinct)


def test_all_initial_aggregations_ignore_nulls():
    # Required for the CASE WHEN fallback of per-metric filters (DESIGN.md §6.2).
    assert all(spec.ignores_nulls for spec in AGGREGATIONS.values())


@pytest.mark.parametrize("name, measure_class", [
    ("sum", "additive"),
    ("count", "additive"),
    ("count_distinct", "distinct_count"),
    ("min", "non_additive"),
    ("max", "non_additive"),
    ("avg", "non_additive"),
])
def test_consistent_measure_class(name, measure_class):
    assert AGGREGATIONS[name].accepts_measure_class(measure_class)


@pytest.mark.parametrize("name, measure_class", [
    ("sum", "distinct_count"),
    ("count_distinct", "additive"),
    ("avg", "additive"),
    ("sum", "whatever"),
])
def test_inconsistent_measure_class(name, measure_class):
    assert not AGGREGATIONS[name].accepts_measure_class(measure_class)
