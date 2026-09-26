"""Semantic model -> Catalog (DESIGN.md §5).

The Catalog is the compiler's symbol table: every dataset, metric, dimension (with type and
physical column) and relationship the customer declared. It is the only knowledge the compiler
has of the warehouse, and the source of every "acceptable value" in a contract.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from walt_compiler.aggregations import AGGREGATIONS, AggregationSpec
from walt_compiler.errors import InvalidSemanticModel, UnsupportedAggregation
from walt_compiler.options import CompileOptions
from walt_compiler.schema import Arr, Field, Obj, Str, pointer, validate
from walt_compiler.suggest import suggest
from walt_compiler.types import TYPES

CARDINALITIES = ("many_to_one", "one_to_one", "one_to_many", "many_to_many")

MODEL_RULE = Obj({
    "datasets": Field(Arr(Obj({
        "name": Field(Str()),
        "grain": Field(Str(), required=False),
    }), min_items=1)),
    "relationships": Field(Arr(Obj({
        "from": Field(Str()),
        "from_column": Field(Str()),
        "to": Field(Str()),
        "to_column": Field(Str()),
        "cardinality": Field(Str(enum=CARDINALITIES)),
    })), required=False),
    "metrics": Field(Arr(Obj({
        "name": Field(Str()),
        "agg": Field(Str()),
        "expression": Field(Str()),
        "model": Field(Str()),
        "measure_class": Field(Str(), required=False),
    }))),
    "dimensions": Field(Arr(Obj({
        "name": Field(Str()),
        "model": Field(Str()),
        "type": Field(Str(enum=tuple(sorted(TYPES)))),
        "column": Field(Str(), required=False),
    }))),
})


@dataclass(frozen=True)
class Dataset:
    name: str
    grain: str | None


@dataclass(frozen=True)
class Relationship:
    from_dataset: str
    from_column: str
    to_dataset: str
    to_column: str
    cardinality: str


@dataclass(frozen=True)
class Metric:
    name: str
    agg: AggregationSpec
    column: str
    dataset: str
    measure_class: str | None


@dataclass(frozen=True)
class Dimension:
    name: str
    dataset: str
    column: str
    type: str


@dataclass(frozen=True)
class Catalog:
    datasets: Mapping[str, Dataset]
    relationships: tuple[Relationship, ...]
    metrics: Mapping[str, Metric]
    dimensions: Mapping[str, Dimension]


def build_catalog(model, options: CompileOptions = CompileOptions()) -> Catalog:
    validate(model, MODEL_RULE, error=InvalidSemanticModel, options=options)

    def near(key, candidates):
        return suggest(key, candidates, options.suggestion_threshold, options.max_suggestions)

    def unique_names(section):
        seen = set()
        for i, entry in enumerate(model.get(section, [])):
            if entry["name"] in seen:
                raise InvalidSemanticModel(f"duplicate {section[:-1]} name {entry['name']!r}.",
                                           path=pointer(section, i, "name"))
            seen.add(entry["name"])

    def declared_dataset(name, *at):
        if name not in datasets:
            raise InvalidSemanticModel(f"{name!r} is not a declared dataset.", path=pointer(*at),
                                       suggestions=near(name, datasets))

    for section in ("datasets", "metrics", "dimensions"):
        unique_names(section)

    datasets = {d["name"]: Dataset(d["name"], d.get("grain")) for d in model["datasets"]}

    metrics = {}
    for i, m in enumerate(model["metrics"]):
        declared_dataset(m["model"], "metrics", i, "model")
        agg = AGGREGATIONS.get(m["agg"])
        if agg is None:
            raise UnsupportedAggregation(f"{m['agg']!r} is not a supported aggregation.",
                                         path=pointer("metrics", i, "agg"),
                                         suggestions=near(m["agg"], AGGREGATIONS))
        measure_class = m.get("measure_class")
        if measure_class is not None and not agg.accepts_measure_class(measure_class):
            raise InvalidSemanticModel(
                f"measure_class {measure_class!r} is inconsistent with agg {agg.name!r} "
                f"(expected: {', '.join(sorted(agg.measure_classes))}).",
                path=pointer("metrics", i, "measure_class"))
        metrics[m["name"]] = Metric(m["name"], agg, m["expression"], m["model"], measure_class)

    dimensions = {}
    for i, d in enumerate(model["dimensions"]):
        declared_dataset(d["model"], "dimensions", i, "model")
        dimensions[d["name"]] = Dimension(d["name"], d["model"], d.get("column", d["name"]), d["type"])

    relationships = []
    for i, r in enumerate(model.get("relationships", [])):
        declared_dataset(r["from"], "relationships", i, "from")
        declared_dataset(r["to"], "relationships", i, "to")
        if r["from"] == r["to"]:
            raise InvalidSemanticModel("a relationship from a dataset to itself is not supported.",
                                       path=pointer("relationships", i, "to"))
        relationships.append(Relationship(r["from"], r["from_column"], r["to"], r["to_column"],
                                          r["cardinality"]))

    return Catalog(MappingProxyType(datasets), tuple(relationships),
                   MappingProxyType(metrics), MappingProxyType(dimensions))
