"""LogicalPlan: the dialect-free IR between resolve and lowering (DESIGN.md §7).

Everything is a frozen dataclass holding tuples, so plans compare by value and nothing is
ever iterated in a hash-dependent order.
"""
from dataclasses import dataclass

from walt_compiler.aggregations import AggregationSpec
from walt_compiler.joins import JoinStep
from walt_compiler.types import TypedLiteral


@dataclass(frozen=True)
class ColumnRef:
    dataset: str
    column: str
    type: str | None        # declared dimension type; None for metric columns (never declared)


# --- predicates ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Compare:
    column: ColumnRef
    op: str
    literal: TypedLiteral


@dataclass(frozen=True)
class InList:
    column: ColumnRef
    literals: tuple[TypedLiteral, ...]


@dataclass(frozen=True)
class And:
    items: tuple["Predicate", ...]


@dataclass(frozen=True)
class Or:
    items: tuple["Predicate", ...]


@dataclass(frozen=True)
class Not:
    item: "Predicate"


Predicate = Compare | InList | And | Or | Not


# --- inner level: grouping and aggregation ---------------------------------------------------

@dataclass(frozen=True)
class GroupKey:
    name: str               # output name
    column: ColumnRef


@dataclass(frozen=True)
class Measure:
    alias: str
    agg: AggregationSpec
    arg: ColumnRef
    condition: Predicate | None     # per-metric filter -> conditional aggregation


# --- outer level: projection -----------------------------------------------------------------

@dataclass(frozen=True)
class GroupRef:
    name: str


@dataclass(frozen=True)
class MeasureRef:
    alias: str


@dataclass(frozen=True)
class Sub:
    left: MeasureRef
    right: MeasureRef


@dataclass(frozen=True)
class PctChange:
    current: MeasureRef
    base: MeasureRef


@dataclass(frozen=True)
class IsTotal:
    pass


OutputExpr = GroupRef | MeasureRef | Sub | PctChange | IsTotal


@dataclass(frozen=True)
class OutputColumn:
    name: str
    expr: OutputExpr


@dataclass(frozen=True)
class OrderKey:
    output: str             # ascending; NULLS LAST unless the output can never be NULL (is_total)


@dataclass(frozen=True)
class LogicalPlan:
    base: str
    joins: tuple[JoinStep, ...]
    group_by: tuple[GroupKey, ...]
    measures: tuple[Measure, ...]
    where: Predicate | None
    grouping_sets: tuple[tuple[str, ...], ...] | None     # group output names per set; None = plain GROUP BY
    outputs: tuple[OutputColumn, ...]
    order_by: tuple[OrderKey, ...]
