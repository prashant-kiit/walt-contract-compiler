"""Dialect: renders the SQL AST to text (DESIGN.md §8).

The base class spells standard SQL. A dialect subclass overrides only what it spells
differently, and declares capabilities (e.g. FILTER on aggregates) instead of branching on
contract features. Nothing here knows what a contract is.
"""
from decimal import Decimal

from walt_compiler.errors import UnsupportedFeature
from walt_compiler.sqlast import (Aggregate, Alias, BinOp, BoolOp, Column, Grouping, GroupingSets, InList, Join,
                                  Literal, Not, OrderItem, Select, Subquery, Table)
from walt_compiler.types import TypedLiteral

_PRECEDENCE = {"OR": 1, "AND": 2, "NOT": 3,
               "=": 4, "!=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4, "IN": 4,
               "+": 5, "-": 5, "*": 6, "/": 6}
_ATOM = 9
_SPELLING = {"!=": "<>"}
_INDENT = "  "


class Dialect:
    name = "standard"
    supports_aggregate_filter = True
    supports_grouping_sets = True

    # --- leaves ---------------------------------------------------------------------------

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def render_literal(self, lit: TypedLiteral) -> str:
        v = lit.value
        match lit.type:
            case "text":
                return "'" + v.replace("'", "''") + "'"
            case "integer":
                return str(v)
            case "double":
                return repr(float(v))
            case "decimal":
                return format(Decimal(v), "f")
            case "date":
                return f"DATE '{v.isoformat()}'"
            case "timestamp":
                return f"TIMESTAMP '{v.isoformat(sep=' ')}'"
            case "boolean":
                return "TRUE" if v else "FALSE"
        raise ValueError(f"no rendering for literal type {lit.type!r}")

    # --- expressions ----------------------------------------------------------------------

    def render(self, node) -> str:
        if isinstance(node, Select):
            return self.render_select(node, 0)
        return getattr(self, f"_render_{type(node).__name__}")(node)

    def _precedence(self, node) -> int:
        if isinstance(node, (BinOp, BoolOp)):
            return _PRECEDENCE[node.op]
        if isinstance(node, Not):
            return _PRECEDENCE["NOT"]
        if isinstance(node, InList):
            return _PRECEDENCE["IN"]
        return _ATOM

    def _operand(self, node, parent: int, strict: bool = False) -> str:
        """Render a child, parenthesised if it binds looser than its parent (or equally, when
        `strict`, i.e. the right side of a non-associative operator)."""
        text = self.render(node)
        p = self._precedence(node)
        return f"({text})" if p < parent or (strict and p == parent) else text

    def _render_Column(self, node: Column) -> str:
        name = self.quote_ident(node.name)
        return f"{self.quote_ident(node.table)}.{name}" if node.table else name

    def _render_Literal(self, node: Literal) -> str:
        return self.render_literal(node.value)

    def _render_BinOp(self, node: BinOp) -> str:
        p = _PRECEDENCE[node.op]
        left = self._operand(node.left, p)
        right = self._operand(node.right, p, strict=node.op in ("-", "/"))
        return f"{left} {_SPELLING.get(node.op, node.op)} {right}"

    def _render_BoolOp(self, node: BoolOp) -> str:
        p = _PRECEDENCE[node.op]
        return f" {node.op} ".join(self._operand(item, p) for item in node.items)

    def _render_Not(self, node: Not) -> str:
        return f"NOT {self._operand(node.item, _PRECEDENCE['NOT'])}"

    def _render_InList(self, node: InList) -> str:
        items = ", ".join(self.render(i) for i in node.items)
        return f"{self._operand(node.expr, _PRECEDENCE['IN'])} IN ({items})"

    def _render_Aggregate(self, node: Aggregate) -> str:
        arg = self.render(node.arg)
        suffix = ""
        if node.filter is not None:
            condition = self.render(node.filter)
            if self.supports_aggregate_filter:
                suffix = f" FILTER (WHERE {condition})"
            elif node.ignores_nulls:
                arg = f"CASE WHEN {condition} THEN {arg} END"
            else:
                raise UnsupportedFeature(
                    f"{self.name} has no FILTER clause and {node.function} does not ignore NULLs, "
                    f"so a per-metric filter cannot be expressed exactly.")
        distinct = "DISTINCT " if node.distinct else ""
        return f"{node.function}({distinct}{arg}){suffix}"

    def _render_Grouping(self, node: Grouping) -> str:
        return f"GROUPING({self.render(node.column)})"

    def _render_Alias(self, node: Alias) -> str:
        return f"{self.render(node.expr)} AS {self.quote_ident(node.name)}"

    # --- statements -----------------------------------------------------------------------

    def render_select(self, sel: Select, depth: int) -> str:
        pad = _INDENT * depth
        lines = [f"{pad}SELECT"]
        lines += [f"{pad}{_INDENT}{self.render(item)}{',' if i < len(sel.items) - 1 else ''}"
                  for i, item in enumerate(sel.items)]
        if isinstance(sel.from_, Subquery):
            lines.append(f"{pad}FROM (")
            lines.append(self.render_select(sel.from_.select, depth + 1))
            lines.append(f"{pad}) AS {self.quote_ident(sel.from_.alias)}")
        else:
            lines.append(f"{pad}FROM {self.quote_ident(sel.from_.name)}")
        lines += [f"{pad}{self._render_join(j)}" for j in sel.joins]
        if sel.where is not None:
            lines.append(f"{pad}WHERE {self.render(sel.where)}")
        if isinstance(sel.group_by, GroupingSets):
            lines.append(f"{pad}GROUP BY {self._render_grouping_sets(sel.group_by)}")
        elif sel.group_by:
            lines.append(f"{pad}GROUP BY {', '.join(self.render(e) for e in sel.group_by)}")
        if sel.order_by:
            lines.append(f"{pad}ORDER BY {', '.join(self._render_order(o) for o in sel.order_by)}")
        return "\n".join(lines)

    def _render_join(self, join: Join) -> str:
        return f"LEFT JOIN {self.quote_ident(join.table.name)} ON {self.render(join.on)}"

    def _render_grouping_sets(self, gs: GroupingSets) -> str:
        if not self.supports_grouping_sets:
            raise UnsupportedFeature(f"{self.name} does not support GROUPING SETS, which totals require.")
        sets = ", ".join("(" + ", ".join(self.render(e) for e in s) + ")" for s in gs.sets)
        return f"GROUPING SETS ({sets})"

    def _render_order(self, item: OrderItem) -> str:
        return f"{self.render(item.expr)} ASC{' NULLS LAST' if item.nulls_last else ''}"
