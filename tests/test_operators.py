"""S2: filter-operator registry (DESIGN.md §5)."""
import pytest

from walt_compiler.operators import OPERATORS
from walt_compiler.types import TYPES


def test_registry_holds_exactly_the_designed_operators():
    assert sorted(OPERATORS) == ["!=", "<", "<=", "=", ">", ">=", "in"]


def test_registry_is_read_only():
    with pytest.raises(TypeError):
        OPERATORS["like"] = object()


def test_only_in_takes_a_list():
    assert [op for op, spec in sorted(OPERATORS.items()) if spec.takes_list] == ["in"]


@pytest.mark.parametrize("op", ["=", "!=", "in"])
def test_equality_operators_allow_every_type(op):
    assert all(OPERATORS[op].allows(spec) for spec in TYPES.values())


@pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
def test_ordering_operators_reject_boolean_only(op):
    rejected = sorted(name for name, spec in TYPES.items() if not OPERATORS[op].allows(spec))
    assert rejected == ["boolean"]
