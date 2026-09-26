"""Aggregation registry (DESIGN.md §5).

All aggregations are correct under totals because grouping sets recompute every aggregate
against the base rows. `ignores_nulls` must be true for the CASE WHEN fallback of per-metric
filters (DESIGN.md §6.2) to give the same answer as FILTER (WHERE ...).
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class AggregationSpec:
    name: str
    function: str
    distinct: bool
    ignores_nulls: bool
    measure_classes: frozenset[str]

    def accepts_measure_class(self, measure_class: str) -> bool:
        return measure_class in self.measure_classes


_ADDITIVE = frozenset({"additive"})
_DISTINCT = frozenset({"distinct_count"})
_NON_ADDITIVE = frozenset({"non_additive"})

AGGREGATIONS = MappingProxyType({spec.name: spec for spec in (
    AggregationSpec("sum", "SUM", distinct=False, ignores_nulls=True, measure_classes=_ADDITIVE),
    AggregationSpec("count", "COUNT", distinct=False, ignores_nulls=True, measure_classes=_ADDITIVE),
    AggregationSpec("count_distinct", "COUNT", distinct=True, ignores_nulls=True, measure_classes=_DISTINCT),
    AggregationSpec("min", "MIN", distinct=False, ignores_nulls=True, measure_classes=_NON_ADDITIVE),
    AggregationSpec("max", "MAX", distinct=False, ignores_nulls=True, measure_classes=_NON_ADDITIVE),
    AggregationSpec("avg", "AVG", distinct=False, ignores_nulls=True, measure_classes=_NON_ADDITIVE),
)})
