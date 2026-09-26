"""Lowering: LogicalPlan -> SQL AST (DESIGN.md §6.6).

Always two levels: an inner query that joins, filters, groups and aggregates, and an outer
projection that computes derived columns and orders. One code path for every contract.
"""
from walt_compiler import plan as p
from walt_compiler import sqlast as s
from walt_compiler.types import TypedLiteral

INNER_ALIAS = "agg"
_HUNDRED = s.Literal(TypedLiteral(100.0, "double"))


def _column(ref: p.ColumnRef) -> s.Column:
    return s.Column(ref.dataset, ref.column)


def _predicate(pred) -> s.Expr | None:
    match pred:
        case None:
            return None
        case p.Compare(column, op, literal):
            return s.BinOp(op, _column(column), s.Literal(literal))
        case p.InList(column, literals):
            return s.InList(_column(column), tuple(s.Literal(lit) for lit in literals))
        case p.And(items):
            return s.BoolOp("AND", tuple(_predicate(i) for i in items))
        case p.Or(items):
            return s.BoolOp("OR", tuple(_predicate(i) for i in items))
        case p.Not(item):
            return s.Not(_predicate(item))
    raise TypeError(f"unknown predicate {pred!r}")


def _output(out: p.OutputColumn) -> s.Expr | s.Alias:
    def ref(r):
        return s.Column(None, r.name if isinstance(r, p.GroupRef) else r.alias)

    match out.expr:
        case p.GroupRef() | p.MeasureRef() as r:
            col = ref(r)
            return col if col.name == out.name else s.Alias(col, out.name)
        case p.Sub(left, right):
            return s.Alias(s.BinOp("-", ref(left), ref(right)), out.name)
        case p.PctChange(current, base):
            change = s.BinOp("-", ref(current), ref(base))
            return s.Alias(s.BinOp("/", s.BinOp("*", _HUNDRED, change), ref(base)), out.name)
        case p.IsTotal():
            return s.Column(None, out.name)
    raise TypeError(f"unknown output {out!r}")


def lower(plan: p.LogicalPlan) -> s.Select:
    group_items = tuple(s.Alias(_column(g.column), g.name) for g in plan.group_by)
    measure_items = tuple(
        s.Alias(s.Aggregate(m.agg.function, _column(m.arg), m.agg.distinct, m.agg.ignores_nulls,
                            _predicate(m.condition)), m.alias)
        for m in plan.measures)
    inner = s.Select(
        items=group_items + measure_items,
        from_=s.Table(plan.base),
        joins=tuple(s.Join(s.Table(j.to_dataset),
                           s.BinOp("=", s.Column(j.from_dataset, j.from_column), s.Column(j.to_dataset, j.to_column)))
                    for j in plan.joins),
        where=_predicate(plan.where),
        group_by=tuple(_column(g.column) for g in plan.group_by),
    )
    return s.Select(
        items=tuple(_output(o) for o in plan.outputs),
        from_=s.Subquery(inner, INNER_ALIAS),
        order_by=tuple(s.OrderItem(s.Column(None, k.output)) for k in plan.order_by),
    )
