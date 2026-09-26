"""SQL AST: SQL structure without dialect spelling (DESIGN.md §8).

Lowering builds these nodes from a LogicalPlan; a Dialect renders them to text.
"""
from dataclasses import dataclass

from walt_compiler.types import TypedLiteral


# --- expressions -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    table: str | None
    name: str


@dataclass(frozen=True)
class Literal:
    value: TypedLiteral


@dataclass(frozen=True)
class BinOp:
    op: str                 # comparison or arithmetic: = != < <= > >= + - * /
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class BoolOp:
    op: str                 # AND | OR
    items: tuple["Expr", ...]


@dataclass(frozen=True)
class Not:
    item: "Expr"


@dataclass(frozen=True)
class InList:
    expr: "Expr"
    items: tuple["Expr", ...]


@dataclass(frozen=True)
class Aggregate:
    function: str
    arg: "Expr"
    distinct: bool
    ignores_nulls: bool     # needed to decide whether the CASE WHEN fallback is exact
    filter: "Expr | None"


@dataclass(frozen=True)
class Grouping:
    column: "Expr"


@dataclass(frozen=True)
class Func:
    name: str               # a scalar function every dialect spells the same way, e.g. NULLIF
    args: tuple["Expr", ...]


Expr = Column | Literal | BinOp | BoolOp | Not | InList | Aggregate | Grouping | Func


# --- query structure -------------------------------------------------------------------------

@dataclass(frozen=True)
class Alias:
    expr: Expr
    name: str


@dataclass(frozen=True)
class Table:
    name: str


@dataclass(frozen=True)
class Subquery:
    select: "Select"
    alias: str


@dataclass(frozen=True)
class Join:
    table: Table
    on: Expr                # always a LEFT JOIN (DESIGN.md §6.1)


@dataclass(frozen=True)
class GroupingSets:
    sets: tuple[tuple[Expr, ...], ...]


@dataclass(frozen=True)
class OrderItem:
    expr: Expr              # ascending
    nulls_last: bool = True


@dataclass(frozen=True)
class Select:
    items: tuple[Expr | Alias, ...]
    from_: Table | Subquery
    joins: tuple[Join, ...] = ()
    where: Expr | None = None
    group_by: tuple[Expr, ...] | GroupingSets = ()
    order_by: tuple[OrderItem, ...] = ()
