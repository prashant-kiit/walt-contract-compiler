"""Contract JSON -> typed Contract tree (DESIGN.md §6, syntactic analysis).

Parsing checks shape only and never looks at the semantic model. Every node keeps the JSON
pointer it came from so that resolve can report precise errors against the Catalog.
"""
from dataclasses import dataclass
from typing import Any

from walt_compiler.errors import InvalidContract, UnsupportedFeature, UnsupportedOperator
from walt_compiler.operators import OPERATORS
from walt_compiler.options import CompileOptions
from walt_compiler.schema import AnyValue, Arr, Field, Obj, Str, pointer, validate
from walt_compiler.suggest import suggest

COMPARE_OUTPUTS = ("values", "delta", "pct_change")
TOTALS = ("grand",)

_FILTER = Obj({
    "field": Field(Str()),
    "op": Field(Str()),
    "value": Field(AnyValue()),
    "model": Field(Str(), required=False),
})

CONTRACT_RULE = Obj({
    "metrics": Field(Arr(Obj({
        "name": Field(Str()),
        "as": Field(Str(), required=False),
        "filters": Field(Arr(_FILTER), required=False),
    }), min_items=1)),
    "group_by": Field(Arr(Str(), unique=True), required=False),
    "filters": Field(Arr(_FILTER), required=False),
    "compare": Field(Obj({
        "dimension": Field(Str()),
        "periods": Field(Arr(AnyValue())),
        "primary": Field(AnyValue()),
        "outputs": Field(Arr(Str(enum=COMPARE_OUTPUTS), min_items=1, unique=True)),
    }), required=False),
    "totals": Field(Str(), required=False),
})


@dataclass(frozen=True)
class FilterSpec:
    field: str
    op: str
    value: Any              # a JSON scalar, or a tuple of scalars for list operators
    model: str | None
    path: str


@dataclass(frozen=True)
class MetricRequest:
    name: str
    alias: str | None
    filters: tuple[FilterSpec, ...]
    path: str


@dataclass(frozen=True)
class CompareSpec:
    dimension: str
    periods: tuple[Any, ...]
    primary: Any
    outputs: tuple[str, ...]
    path: str


@dataclass(frozen=True)
class Contract:
    metrics: tuple[MetricRequest, ...]
    group_by: tuple[str, ...]
    filters: tuple[FilterSpec, ...]
    compare: CompareSpec | None
    totals: str | None


def _is_scalar(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def parse_contract(doc, options: CompileOptions = CompileOptions()) -> Contract:
    validate(doc, CONTRACT_RULE, error=InvalidContract, options=options)

    def near(key, candidates):
        return suggest(key, candidates, options.suggestion_threshold, options.max_suggestions)

    def scalar(value, *at):
        if not _is_scalar(value):
            raise InvalidContract("expected a single value (string, number, boolean or null).",
                                  path=pointer(*at))
        return value

    def parse_filter(raw, *at) -> FilterSpec:
        spec = OPERATORS.get(raw["op"])
        if spec is None:
            raise UnsupportedOperator(f"{raw['op']!r} is not a supported operator.",
                                      path=pointer(*at, "op"), suggestions=near(raw["op"], OPERATORS))
        value = raw["value"]
        if spec.takes_list:
            if not isinstance(value, list) or not value:
                raise InvalidContract(f"operator {spec.symbol!r} requires a non-empty list.",
                                      path=pointer(*at, "value"))
            value = tuple(scalar(v, *at, "value", i) for i, v in enumerate(value))
        else:
            value = scalar(value, *at, "value")
        return FilterSpec(raw["field"], spec.symbol, value, raw.get("model"), pointer(*at))

    metrics = tuple(
        MetricRequest(m["name"], m.get("as"),
                      tuple(parse_filter(f, "metrics", i, "filters", j) for j, f in enumerate(m.get("filters", []))),
                      pointer("metrics", i))
        for i, m in enumerate(doc["metrics"]))
    filters = tuple(parse_filter(f, "filters", i) for i, f in enumerate(doc.get("filters", [])))
    group_by = tuple(doc.get("group_by", []))

    compare = None
    if "compare" in doc:
        c = doc["compare"]
        compare = CompareSpec(
            c["dimension"],
            tuple(scalar(p, "compare", "periods", i) for i, p in enumerate(c["periods"])),
            scalar(c["primary"], "compare", "primary"),
            tuple(c["outputs"]),
            pointer("compare"))

    totals = doc.get("totals")
    if totals is not None:
        if totals not in TOTALS:
            raise UnsupportedFeature(f"totals {totals!r} is not supported (supported: {', '.join(TOTALS)}).",
                                     path=pointer("totals"), suggestions=near(totals, TOTALS))
        if not group_by:
            raise InvalidContract("totals needs a non-empty group_by: an ungrouped result is already the total.",
                                  path=pointer("totals"))

    return Contract(metrics, group_by, filters, compare, totals)
