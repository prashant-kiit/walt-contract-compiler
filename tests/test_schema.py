"""S3: the declarative shape-rule interpreter (DESIGN.md §3, syntactic analysis).

Uses small throwaway rules so the interpreter is tested independently of the real model/contract rules.
"""
import pytest

from walt_compiler.errors import InvalidContract, InvalidSemanticModel
from walt_compiler.options import CompileOptions
from walt_compiler.schema import AnyValue, Arr, Field, Obj, Str, pointer, validate

RULE = Obj({
    "name": Field(Str()),
    "kind": Field(Str(enum=("many_to_one", "one_to_one")), required=False),
    "tags": Field(Arr(Str(), unique=True), required=False),
    "items": Field(Arr(Obj({"expression": Field(Str()), "value": Field(AnyValue(), required=False)}),
                       min_items=1), required=False),
})


def check(doc, error=InvalidContract, options=CompileOptions()):
    with pytest.raises(error) as exc:
        validate(doc, RULE, error=error, options=options)
    return exc.value


def test_valid_document_passes():
    validate({"name": "x", "kind": "one_to_one", "tags": ["a", "b"],
              "items": [{"expression": "e", "value": [1, None, {"k": True}]}]},
             RULE, error=InvalidContract, options=CompileOptions())


def test_raises_the_requested_error_class():
    assert isinstance(check([], error=InvalidSemanticModel), InvalidSemanticModel)


@pytest.mark.parametrize("doc, path, kind", [
    ([], "", "array"),
    ({"name": 3}, "/name", "number"),
    ({"name": True}, "/name", "boolean"),
    ({"name": None}, "/name", "null"),
    ({"name": "x", "tags": "a"}, "/tags", "string"),
    ({"name": "x", "items": [{"expression": {}}]}, "/items/0/expression", "object"),
])
def test_wrong_kind_reports_path_and_actual_kind(doc, path, kind):
    err = check(doc)
    assert err.path == path
    assert f"got {kind}" in err.message


def test_missing_required_key_points_at_the_key():
    err = check({"name": "x", "items": [{"value": 1}]})
    assert err.path == "/items/0/expression"
    assert "missing required key 'expression'" in err.message


def test_unknown_key_with_suggestion():
    err = check({"name": "x", "items": [{"expresion": "e"}]})
    assert err.path == "/items/0/expresion"
    assert "unknown key 'expresion'" in err.message
    assert err.suggestions == ("expression",)


def test_unknown_key_suggestion_respects_threshold_option():
    err = check({"name": "x", "items": [{"expresion": "e"}]},
                options=CompileOptions(suggestion_threshold=0.0))
    assert err.suggestions == ()


def test_unknown_key_reported_before_missing_key():
    err = check({"nme": "x"})
    assert err.path == "/nme"
    assert err.suggestions == ("name",)


def test_enum_mismatch_lists_allowed_values_and_suggests():
    err = check({"name": "x", "kind": "many-to-one"})
    assert err.path == "/kind"
    assert "many_to_one" in err.message and "one_to_one" in err.message
    assert err.suggestions == ("many_to_one",)


def test_empty_string_rejected():
    assert check({"name": ""}).path == "/name"


def test_min_items():
    assert check({"name": "x", "items": []}).path == "/items"


def test_unique_items_points_at_the_duplicate():
    err = check({"name": "x", "tags": ["a", "b", "a"]})
    assert err.path == "/tags/2"
    assert "duplicate" in err.message


@pytest.mark.parametrize("parts, expected", [
    ((), ""),
    (("metrics", 0, "name"), "/metrics/0/name"),
    (("a/b", "c~d"), "/a~1b/c~0d"),       # RFC 6901 escaping
])
def test_json_pointer(parts, expected):
    assert pointer(*parts) == expected
