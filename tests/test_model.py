"""S3: semantic model -> Catalog, with cross-reference checks (DESIGN.md §5)."""
import copy

import pytest

from walt_compiler.errors import InvalidSemanticModel, UnsupportedAggregation
from walt_compiler.model import build_catalog
from tests.helpers import pdf_model


def model_with(mutate):
    m = copy.deepcopy(pdf_model())
    mutate(m)
    return m


def fails(mutate, error=InvalidSemanticModel):
    with pytest.raises(error) as exc:
        build_catalog(model_with(mutate))
    return exc.value


# --- happy path -------------------------------------------------------------------------------

def test_pdf_model_builds_catalog():
    cat = build_catalog(pdf_model())
    assert list(cat.datasets) == ["fact_sales", "dim_store", "dim_calendar"]
    assert cat.datasets["fact_sales"].grain == "one order line"

    rev = cat.metrics["total_revenue"]
    assert (rev.name, rev.agg.name, rev.column, rev.dataset, rev.measure_class) == \
        ("total_revenue", "sum", "revenue", "fact_sales", "additive")
    cnt = cat.metrics["order_count"]
    assert (cnt.agg.name, cnt.agg.distinct, cnt.column) == ("count_distinct", True, "order_id")

    assert list(cat.dimensions) == ["region", "channel", "fiscal_year"]
    fy = cat.dimensions["fiscal_year"]
    assert (fy.name, fy.dataset, fy.column, fy.type) == ("fiscal_year", "dim_calendar", "fiscal_year", "integer")

    r0, r1 = cat.relationships
    assert (r0.from_dataset, r0.from_column, r0.to_dataset, r0.to_column, r0.cardinality) == \
        ("fact_sales", "store_id", "dim_store", "store_id", "many_to_one")
    assert (r1.from_dataset, r1.from_column, r1.to_dataset, r1.to_column) == \
        ("fact_sales", "order_date", "dim_calendar", "date")


def test_catalog_is_read_only():
    cat = build_catalog(pdf_model())
    with pytest.raises(TypeError):
        cat.metrics["x"] = None
    with pytest.raises(AttributeError):
        cat.metrics = {}


def test_dimension_column_defaults_to_name_and_can_differ():
    m = model_with(lambda m: m["dimensions"].append(
        {"name": "store_region", "model": "dim_store", "type": "text", "column": "region"}))
    cat = build_catalog(m)
    assert cat.dimensions["region"].column == "region"
    assert cat.dimensions["store_region"].column == "region"


def test_optional_fields_may_be_omitted():
    def strip(m):
        del m["relationships"]
        del m["metrics"][0]["measure_class"]
        del m["datasets"][0]["grain"]
        m["dimensions"] = [d for d in m["dimensions"] if d["model"] == "fact_sales"]
    cat = build_catalog(model_with(strip))
    assert cat.relationships == ()
    assert cat.metrics["total_revenue"].measure_class is None
    assert cat.datasets["fact_sales"].grain is None


def test_metric_and_dimension_may_share_a_name():
    # Separate namespaces: the contract always says which one it means.
    m = model_with(lambda m: m["dimensions"].append(
        {"name": "total_revenue", "model": "fact_sales", "type": "double", "column": "revenue"}))
    build_catalog(m)


# --- shape (syntactic) errors ----------------------------------------------------------------

def test_model_must_be_an_object():
    with pytest.raises(InvalidSemanticModel) as exc:
        build_catalog([])
    assert exc.value.path == ""


def test_unknown_top_level_key_is_suggested():
    err = fails(lambda m: m.update(dimension=m.pop("dimensions")))
    assert err.path == "/dimension"
    assert err.suggestions == ("dimensions",)


def test_missing_dimension_type():
    err = fails(lambda m: m["dimensions"][0].pop("type"))
    assert err.path == "/dimensions/0/type"


def test_unknown_dimension_type_is_suggested():
    err = fails(lambda m: m["dimensions"][2].update(type="intger"))
    assert err.path == "/dimensions/2/type"
    assert err.suggestions == ("integer",)


def test_bad_cardinality_is_suggested():
    err = fails(lambda m: m["relationships"][0].update(cardinality="many-to-one"))
    assert err.path == "/relationships/0/cardinality"
    assert err.suggestions == ("many_to_one",)


def test_datasets_must_not_be_empty():
    assert fails(lambda m: m.update(datasets=[])).path == "/datasets"


# --- cross-reference (semantic) errors -------------------------------------------------------

@pytest.mark.parametrize("section, index", [("datasets", 1), ("metrics", 1), ("dimensions", 2)])
def test_duplicate_names_point_at_the_second_occurrence(section, index):
    def dup(m):
        m[section][index]["name"] = m[section][0]["name"]
    err = fails(dup)
    assert err.path == f"/{section}/{index}/name"
    assert "duplicate" in err.message


def test_metric_on_undeclared_dataset():
    err = fails(lambda m: m["metrics"][0].update(model="fact_sale"))
    assert err.path == "/metrics/0/model"
    assert err.suggestions == ("fact_sales",)


def test_dimension_on_undeclared_dataset():
    err = fails(lambda m: m["dimensions"][0].update(model="dim_stores"))
    assert err.path == "/dimensions/0/model"
    assert err.suggestions == ("dim_store",)


@pytest.mark.parametrize("end", ["from", "to"])
def test_relationship_to_undeclared_dataset(end):
    err = fails(lambda m: m["relationships"][1].update({end: "dim_calender"}))
    assert err.path == f"/relationships/1/{end}"


def test_relationship_to_itself_is_rejected():
    err = fails(lambda m: m["relationships"][0].update(to="fact_sales"))
    assert err.path == "/relationships/0/to"


def test_unknown_aggregation_is_suggested():
    err = fails(lambda m: m["metrics"][0].update(agg="summ"), error=UnsupportedAggregation)
    assert err.path == "/metrics/0/agg"
    assert err.suggestions == ("sum",)


def test_inconsistent_measure_class():
    err = fails(lambda m: m["metrics"][1].update(measure_class="additive"))
    assert err.path == "/metrics/1/measure_class"
