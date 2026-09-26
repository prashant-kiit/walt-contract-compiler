"""Resolve: typed Contract + Catalog -> LogicalPlan (DESIGN.md §3, semantic analysis).

Every name is looked up in the Catalog, every literal coerced to its dimension's declared type,
every operator checked against that type, and every dataset the contract touches is attached to
the base through the join planner. Features expand into plain IR building blocks via PlanBuilder.
"""
from walt_compiler.contract import Contract, FilterSpec, MetricRequest
from walt_compiler.errors import (DuplicateOutputName, ModelMismatch, MultipleFactTables, UnknownDimension,
                                  UnknownMetric, UnsupportedFeature, UnsupportedOperator)
from walt_compiler.features import compare as compare_feature
from walt_compiler.features import metric_filters
from walt_compiler.joins import plan_joins
from walt_compiler.model import Catalog, Dimension, Metric
from walt_compiler.operators import OPERATORS
from walt_compiler.options import CompileOptions
from walt_compiler.plan import (And, ColumnRef, Compare, GroupKey, GroupRef, InList, LogicalPlan, MeasureRef,
                                OrderKey, OutputColumn, Predicate)
from walt_compiler.suggest import suggest
from walt_compiler.types import TYPES, coerce_literal


class PlanBuilder:
    """Accumulates the pieces of a LogicalPlan while the contract is resolved."""

    def __init__(self, catalog: Catalog, options: CompileOptions):
        self.catalog = catalog
        self.options = options
        self.base: str | None = None
        self.needed: dict[str, str] = {}        # dataset -> contract path that first required it
        self.group_by: list[GroupKey] = []
        self.measures = []
        self.where: list[Predicate] = []
        self.grouping_sets = None
        self.group_outputs: list[tuple[OutputColumn, str]] = []    # (output, contract path)
        self.measure_outputs: list[tuple[OutputColumn, str]] = []
        self.extra_outputs: list[tuple[OutputColumn, str]] = []
        self.order_by: list[OrderKey] = []

    def near(self, key, candidates):
        return suggest(key, candidates, self.options.suggestion_threshold, self.options.max_suggestions)

    # --- lookups --------------------------------------------------------------------------

    def metric(self, request: MetricRequest) -> Metric:
        metric = self.catalog.metrics.get(request.name)
        if metric is None:
            raise UnknownMetric(f"{request.name!r} is not a metric.", path=f"{request.path}/name",
                                suggestions=self.near(request.name, self.catalog.metrics))
        return metric

    def dimension(self, name: str, path: str) -> Dimension:
        dim = self.catalog.dimensions.get(name)
        if dim is None:
            raise UnknownDimension(f"{name!r} is not a dimension.", path=path,
                                   suggestions=self.near(name, self.catalog.dimensions))
        return dim

    def column(self, dim: Dimension, path: str) -> ColumnRef:
        """Column reference for a dimension; records that its dataset must be joined."""
        self.needed.setdefault(dim.dataset, path)
        return ColumnRef(dim.dataset, dim.column, dim.type)

    # --- predicates -----------------------------------------------------------------------

    def predicate(self, f: FilterSpec) -> Predicate:
        dim = self.dimension(f.field, f"{f.path}/field")
        if f.model is not None and f.model != dim.dataset:
            raise ModelMismatch(f"dimension {dim.name!r} belongs to {dim.dataset!r}, not {f.model!r}.",
                                path=f"{f.path}/model")
        spec = OPERATORS[f.op]
        if not spec.allows(TYPES[dim.type]):
            raise UnsupportedOperator(f"operator {f.op!r} cannot be applied to {dim.type} dimension {dim.name!r}.",
                                      path=f"{f.path}/op")
        col = self.column(dim, f"{f.path}/field")
        if spec.takes_list:
            return InList(col, tuple(coerce_literal(dim.type, v, f"{f.path}/value/{i}")
                                     for i, v in enumerate(f.value)))
        return Compare(col, f.op, coerce_literal(dim.type, f.value, f"{f.path}/value"))

    @staticmethod
    def conjunction(predicates: list[Predicate]) -> Predicate | None:
        if not predicates:
            return None
        return predicates[0] if len(predicates) == 1 else And(tuple(predicates))

    # --- assembly -------------------------------------------------------------------------

    def outputs(self) -> tuple[OutputColumn, ...]:
        seen = set()
        for output, path in self.group_outputs + self.measure_outputs + self.extra_outputs:
            if output.name in seen:
                raise DuplicateOutputName(f"output column {output.name!r} appears more than once; "
                                          f"give one of them a distinct 'as'.", path=path)
            seen.add(output.name)
        return tuple(o for o, _ in self.group_outputs + self.measure_outputs + self.extra_outputs)

    def build(self) -> LogicalPlan:
        outputs = self.outputs()
        joins = plan_joins(self.catalog, self.base, self.needed)
        return LogicalPlan(self.base, joins, tuple(self.group_by), tuple(self.measures),
                           self.conjunction(self.where), self.grouping_sets, outputs, tuple(self.order_by))


def resolve(contract: Contract, catalog: Catalog, options: CompileOptions = CompileOptions()) -> LogicalPlan:
    b = PlanBuilder(catalog, options)

    for request in contract.metrics:
        metric = b.metric(request)
        if b.base is None:
            b.base = metric.dataset
        elif metric.dataset != b.base:
            raise MultipleFactTables(
                f"metric {metric.name!r} is on {metric.dataset!r} but earlier metrics are on {b.base!r}; "
                f"metrics from different datasets in one query are not supported.", path=f"{request.path}/name")
        m = metric_filters.measure(b, request, metric)
        b.measures.append(m)
        b.measure_outputs.append((OutputColumn(m.alias, MeasureRef(m.alias)),
                                  f"{request.path}/as" if request.alias else f"{request.path}/name"))

    for i, name in enumerate(contract.group_by):
        path = f"/group_by/{i}"
        b.group_by.append(GroupKey(name, b.column(b.dimension(name, path), path)))
        b.group_outputs.append((OutputColumn(name, GroupRef(name)), path))
        b.order_by.append(OrderKey(name))

    b.where.extend(b.predicate(f) for f in contract.filters)

    if contract.compare is not None:
        compare_feature.apply(b, contract)
    if contract.totals is not None:
        raise UnsupportedFeature("totals is not implemented yet.", path="/totals")

    return b.build()
