"""Per-metric filters -> conditional aggregation (DESIGN.md §6.2).

A metric's own filters become the measure's condition, ANDed together. The contract-level
filters go to WHERE and apply to every measure; the two combine as AND ("filter over filter").
"""
from walt_compiler.contract import MetricRequest
from walt_compiler.model import Metric
from walt_compiler.plan import ColumnRef, Measure


def measure(builder, request: MetricRequest, metric: Metric) -> Measure:
    condition = builder.conjunction([builder.predicate(f) for f in request.filters])
    return Measure(request.alias or request.name, metric.agg,
                   ColumnRef(metric.dataset, metric.column, None), condition)
