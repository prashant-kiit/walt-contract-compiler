"""S1: the CompilerError hierarchy (DESIGN.md §9)."""
import pytest

from walt_compiler import errors
from walt_compiler.errors import CompilerError, InvalidContract, UnknownMetric

ALL_CODES = [
    "InvalidSemanticModel", "InvalidContract", "UnknownDialect", "UnknownMetric", "UnknownDimension",
    "UnsupportedOperator", "UnsupportedAggregation", "UnsupportedFeature", "UnsupportedRelationship",
    "InvalidLiteral", "ModelMismatch", "DuplicateOutputName", "NoJoinPath", "AmbiguousJoinPath",
    "MultipleFactTables", "InvalidCompare", "ConflictingFilter", "IdentifierTooLong",
]


@pytest.mark.parametrize("code", ALL_CODES)
def test_every_design_error_code_exists(code):
    cls = getattr(errors, code)
    assert issubclass(cls, CompilerError)
    err = cls("boom")
    assert err.code == code
    assert isinstance(err, Exception)


def test_error_carries_path_message_and_suggestions():
    err = UnknownMetric("'total_revenu' is not a metric.", path="/metrics/0/name",
                        suggestions=["total_revenue"])
    assert err.code == "UnknownMetric"
    assert err.path == "/metrics/0/name"
    assert err.message == "'total_revenu' is not a metric."
    assert err.suggestions == ("total_revenue",)  # stored immutably


def test_defaults_are_empty_path_and_no_suggestions():
    err = InvalidContract("contract must be a JSON object.")
    assert err.path == ""
    assert err.suggestions == ()


def test_str_with_path_and_suggestions():
    err = UnknownMetric("'total_revenu' is not a metric.", path="/metrics/0/name",
                        suggestions=["total_revenue"])
    assert str(err) == ("UnknownMetric at /metrics/0/name: 'total_revenu' is not a metric. "
                        "Did you mean: total_revenue?")


def test_str_with_several_suggestions():
    err = UnknownMetric("'x' is not a metric.", path="/metrics/0/name", suggestions=["a", "b"])
    assert str(err).endswith("Did you mean: a, b?")


def test_str_without_path_or_suggestions():
    assert str(InvalidContract("contract must be a JSON object.")) == \
        "InvalidContract: contract must be a JSON object."


def test_all_codes_catchable_as_compiler_error():
    with pytest.raises(CompilerError):
        raise errors.NoJoinPath("no path from fact_sales to dim_x.")


def test_codes_are_distinct_classes():
    assert len({getattr(errors, c) for c in ALL_CODES}) == len(ALL_CODES)
