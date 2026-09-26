"""Compare -> a two-period pivot over any declared dimension (DESIGN.md §6.3).

Each measure becomes one conditional measure per period (its own condition AND dim = period),
the query is restricted to the two periods, and delta / pct_change are derived outputs computed
in the outer query from the row's own period cells, so a totals row rebuilds them from its own
values rather than summing or averaging the rows above it.
"""
import json

from walt_compiler.contract import Contract
from walt_compiler.errors import ConflictingFilter, InvalidCompare
from walt_compiler.plan import And, Compare, InList, Measure, MeasureRef, OutputColumn, PctChange, Sub
from walt_compiler.types import coerce_literal


def _label(raw) -> str:
    """The period exactly as written in the contract: strings verbatim, other JSON values in JSON form."""
    return raw if isinstance(raw, str) else json.dumps(raw)


def _and(condition, extra):
    if condition is None:
        return extra
    if isinstance(condition, And):
        return And(condition.items + (extra,))
    return And((condition, extra))


def apply(b, contract: Contract) -> None:
    spec = contract.compare
    dim = b.dimension(spec.dimension, f"{spec.path}/dimension")
    col = b.column(dim, f"{spec.path}/dimension")

    if any(g.column == col for g in b.group_by):
        raise InvalidCompare(f"compare dimension {dim.name!r} cannot also be in group_by.",
                             path=f"{spec.path}/dimension")
    filters = [f for m in contract.metrics for f in m.filters] + list(contract.filters)
    for f in filters:
        other = b.catalog.dimensions[f.field]
        if (other.dataset, other.column) == (dim.dataset, dim.column):
            raise ConflictingFilter(f"a filter on compare dimension {dim.name!r} would silently empty a period.",
                                    path=f"{f.path}/field")

    if len(spec.periods) != 2:
        raise InvalidCompare(f"compare needs exactly 2 periods, got {len(spec.periods)}.",
                             path=f"{spec.path}/periods")
    periods = [coerce_literal(dim.type, raw, f"{spec.path}/periods/{i}") for i, raw in enumerate(spec.periods)]
    if periods[0] == periods[1]:
        raise InvalidCompare("the two compare periods must differ.", path=f"{spec.path}/periods/1")
    primary = coerce_literal(dim.type, spec.primary, f"{spec.path}/primary")
    if primary not in periods:
        raise InvalidCompare(f"primary {spec.primary!r} is not one of the compare periods.",
                             path=f"{spec.path}/primary")
    current = periods.index(primary)
    other = 1 - current
    labels = [_label(raw) for raw in spec.periods]

    measures, outputs = [], []
    for m, (_, path) in zip(b.measures, b.measure_outputs):
        per_period = [Measure(f"{m.alias}_{labels[i]}", m.agg, m.arg, _and(m.condition, Compare(col, "=", periods[i])))
                      for i in range(2)]
        measures += per_period
        refs = [MeasureRef(pm.alias) for pm in per_period]
        if "values" in spec.outputs:
            outputs += [(OutputColumn(r.alias, r), path) for r in refs]
        if "delta" in spec.outputs:
            outputs.append((OutputColumn(f"{m.alias}_delta", Sub(refs[current], refs[other])), path))
        if "pct_change" in spec.outputs:
            outputs.append((OutputColumn(f"{m.alias}_pct_change", PctChange(refs[current], refs[other])), path))

    b.measures[:] = measures
    b.measure_outputs[:] = outputs
    b.where.append(InList(col, tuple(periods)))
