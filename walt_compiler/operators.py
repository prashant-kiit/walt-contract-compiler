"""Filter-operator registry (DESIGN.md §5).

A spec only describes an operator. Arity is checked when the contract is parsed; the
operand's type is checked during resolve, once the dimension's declared type is known.
"""
from dataclasses import dataclass
from types import MappingProxyType

from walt_compiler.types import TypeSpec


@dataclass(frozen=True)
class OperatorSpec:
    symbol: str
    takes_list: bool
    needs_orderable: bool

    def allows(self, type_spec: TypeSpec) -> bool:
        return type_spec.orderable or not self.needs_orderable


OPERATORS = MappingProxyType({spec.symbol: spec for spec in (
    OperatorSpec("=", takes_list=False, needs_orderable=False),
    OperatorSpec("!=", takes_list=False, needs_orderable=False),
    OperatorSpec("<", takes_list=False, needs_orderable=True),
    OperatorSpec("<=", takes_list=False, needs_orderable=True),
    OperatorSpec(">", takes_list=False, needs_orderable=True),
    OperatorSpec(">=", takes_list=False, needs_orderable=True),
    OperatorSpec("in", takes_list=True, needs_orderable=False),
)})
