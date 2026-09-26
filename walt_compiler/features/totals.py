"""Totals -> one grand-total row via GROUPING SETS (DESIGN.md §6.4).

The plan asks for two grouping sets: the full group_by and the empty set, so every aggregate on
the totals row is recomputed against the base rows. The totals row is flagged by an is_total
output (placed after every metric column) and sorted last by ordering on is_total first.
"""
from walt_compiler.contract import Contract
from walt_compiler.plan import IsTotal, OrderKey, OutputColumn

IS_TOTAL = "is_total"


def apply(b, contract: Contract) -> None:
    b.grouping_sets = (tuple(g.name for g in b.group_by), ())
    b.extra_outputs.append((OutputColumn(IS_TOTAL, IsTotal()), "/totals"))
    b.order_by.insert(0, OrderKey(IS_TOTAL))
