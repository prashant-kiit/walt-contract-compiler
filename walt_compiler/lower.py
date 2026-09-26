"""Lowering: LogicalPlan -> SQL AST (DESIGN.md §6.6).

Always two levels: an inner query that joins, filters, groups and aggregates, and an outer
projection that computes derived columns and orders. One code path for every contract.
"""
from walt_compiler import plan as p
from walt_compiler import sqlast as s
from walt_compiler.types import TypedLiteral

INNER_ALIAS = "agg"
_HUNDRED = s.Literal(TypedLiteral(100.0, "double"))
_ZERO = s.Literal(TypedLiteral(0, "integer"))


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
            guarded = s.Func("NULLIF", (ref(base), _ZERO))     # a zero base gives NULL (DESIGN.md §6.3)
            return s.Alias(s.BinOp("/", s.BinOp("*", _HUNDRED, change), guarded), out.name)
        case p.IsTotal():
            return s.Column(None, out.name)
    raise TypeError(f"unknown output {out!r}")


def _is_total_item(plan: p.LogicalPlan) -> tuple[s.Alias, ...]:
    """GROUPING(g1) = 1 for the IsTotal output: with only the full set and the empty set, the first
    group column alone tells the totals row apart from a group whose value is NULL."""
    names = [o.name for o in plan.outputs if isinstance(o.expr, p.IsTotal)]
    if not names:
        return ()
    flag = s.BinOp("=", s.Grouping(_column(plan.group_by[0].column)), s.Literal(TypedLiteral(1, "integer")))
    return (s.Alias(flag, names[0]),)


def _group_by(plan: p.LogicalPlan) -> tuple[s.Expr, ...] | s.GroupingSets:
    columns = {g.name: _column(g.column) for g in plan.group_by}
    if plan.grouping_sets is None:
        return tuple(columns.values())
    return s.GroupingSets(tuple(tuple(columns[name] for name in gs) for gs in plan.grouping_sets))


def lower(plan: p.LogicalPlan) -> s.Select:
    group_items = tuple(s.Alias(_column(g.column), g.name) for g in plan.group_by)
    measure_items = tuple(
        s.Alias(s.Aggregate(m.agg.function, _column(m.arg), m.agg.distinct, m.agg.ignores_nulls,
                            _predicate(m.condition)), m.alias)
        for m in plan.measures)
    never_null = {o.name for o in plan.outputs if isinstance(o.expr, p.IsTotal)}
    inner = s.Select(
        items=group_items + measure_items + _is_total_item(plan),
        from_=s.Table(plan.base),
        joins=tuple(s.Join(s.Table(j.to_dataset),
                           s.BinOp("=", s.Column(j.from_dataset, j.from_column), s.Column(j.to_dataset, j.to_column)))
                    for j in plan.joins),
        where=_predicate(plan.where),
        group_by=_group_by(plan),
    )
    return s.Select(
        items=tuple(_output(o) for o in plan.outputs),
        from_=s.Subquery(inner, INNER_ALIAS),
        order_by=tuple(s.OrderItem(s.Column(None, k.output), nulls_last=k.output not in never_null)
                       for k in plan.order_by),
    )
