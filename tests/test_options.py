"""S1: CompileOptions only tunes error reporting (DESIGN.md §1, §9)."""
import dataclasses

import pytest

from walt_compiler.options import CompileOptions


def test_defaults():
    opts = CompileOptions()
    assert opts.suggestion_threshold == 0.4
    assert opts.max_suggestions == 3


def test_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CompileOptions().max_suggestions = 5


@pytest.mark.parametrize("kwargs", [
    {"suggestion_threshold": -0.1},
    {"suggestion_threshold": 1.1},
    {"max_suggestions": -1},
])
def test_rejects_out_of_range_values(kwargs):
    with pytest.raises(ValueError):
        CompileOptions(**kwargs)
