"""S5: join planning over declared relationships (DESIGN.md §6.1).

Uses small in-test models (not the PDF's) so every graph shape is exercised deliberately.
"""
import pytest

from walt_compiler.errors import AmbiguousJoinPath, NoJoinPath, UnsupportedRelationship
from walt_compiler.joins import JoinStep, plan_joins
from walt_compiler.model import build_catalog


def catalog(datasets, relationships):
    return build_catalog({
        "datasets": [{"name": d} for d in datasets],
        "relationships": [
            {"from": f, "from_column": fc, "to": t, "to_column": tc, "cardinality": card}
            for f, fc, t, tc, card in relationships],
        "metrics": [], "dimensions": [],
    })


# fact ─m2o─▶ store ─m2o─▶ region
#  ├───m2o─▶ calendar
#  ├───o2o─▶ detail
#  ├───o2m─▶ lines
#  └───m2m─▶ tags          island (no relationships)
SNOWFLAKE = catalog(
    ["fact", "store", "region", "calendar", "detail", "lines", "tags", "island"],
    [("fact", "store_id", "store", "id", "many_to_one"),
     ("store", "region_id", "region", "id", "many_to_one"),
     ("fact", "day", "calendar", "date", "many_to_one"),
     ("fact", "id", "detail", "fact_id", "one_to_one"),
     ("fact", "id", "lines", "fact_id", "one_to_many"),
     ("fact", "id", "tags", "fact_id", "many_to_many")])

STORE = JoinStep("fact", "store_id", "store", "id")
REGION = JoinStep("store", "region_id", "region", "id")
CALENDAR = JoinStep("fact", "day", "calendar", "date")


def test_direct_join():
    assert plan_joins(SNOWFLAKE, "fact", {"store": "/group_by/0"}) == (STORE,)


def test_multi_hop_join():
    assert plan_joins(SNOWFLAKE, "fact", {"region": "/group_by/0"}) == (STORE, REGION)


def test_only_needed_datasets_are_joined():
    assert plan_joins(SNOWFLAKE, "fact", {"calendar": "/filters/0"}) == (CALENDAR,)


def test_nothing_needed_means_no_joins():
    assert plan_joins(SNOWFLAKE, "fact", {}) == ()
    assert plan_joins(SNOWFLAKE, "fact", {"fact": "/filters/0"}) == ()


def test_order_is_breadth_first_then_declaration_order_not_request_order():
    expected = (STORE, CALENDAR, REGION)
    assert plan_joins(SNOWFLAKE, "fact", {"region": "/a", "calendar": "/b"}) == expected
    assert plan_joins(SNOWFLAKE, "fact", {"calendar": "/b", "region": "/a"}) == expected


def test_shared_prefix_is_joined_once():
    assert plan_joins(SNOWFLAKE, "fact", {"store": "/a", "region": "/b"}) == (STORE, REGION)


def test_one_to_one_is_followed():
    assert plan_joins(SNOWFLAKE, "fact", {"detail": "/a"}) == (JoinStep("fact", "id", "detail", "fact_id"),)


@pytest.mark.parametrize("target, cardinality", [("lines", "one_to_many"), ("tags", "many_to_many")])
def test_fan_out_relationships_are_refused(target, cardinality):
    with pytest.raises(UnsupportedRelationship) as exc:
        plan_joins(SNOWFLAKE, "fact", {target: "/group_by/0"})
    assert exc.value.path == "/group_by/0"
    assert cardinality in exc.value.message


def test_unreachable_dataset():
    with pytest.raises(NoJoinPath) as exc:
        plan_joins(SNOWFLAKE, "fact", {"island": "/filters/2"})
    assert exc.value.path == "/filters/2"
    assert "fact" in exc.value.message and "island" in exc.value.message


def test_relationships_are_never_walked_backwards():
    with pytest.raises(NoJoinPath):
        plan_joins(SNOWFLAKE, "store", {"fact": "/a"})


def test_two_relationships_to_the_same_dataset_are_ambiguous():
    # Role-playing dimension: order date vs ship date.
    cat = catalog(["fact", "calendar"],
                  [("fact", "order_date", "calendar", "date", "many_to_one"),
                   ("fact", "ship_date", "calendar", "date", "many_to_one")])
    with pytest.raises(AmbiguousJoinPath) as exc:
        plan_joins(cat, "fact", {"calendar": "/group_by/0"})
    assert exc.value.path == "/group_by/0"


def test_diamond_is_ambiguous():
    cat = catalog(["fact", "a", "b", "c"],
                  [("fact", "a_id", "a", "id", "many_to_one"), ("fact", "b_id", "b", "id", "many_to_one"),
                   ("a", "c_id", "c", "id", "many_to_one"), ("b", "c_id", "c", "id", "many_to_one")])
    with pytest.raises(AmbiguousJoinPath):
        plan_joins(cat, "fact", {"c": "/a"})


def test_unique_shortest_path_wins_over_a_longer_one():
    cat = catalog(["fact", "a", "c"],
                  [("fact", "a_id", "a", "id", "many_to_one"), ("a", "c_id", "c", "id", "many_to_one"),
                   ("fact", "c_id", "c", "id", "many_to_one")])
    assert plan_joins(cat, "fact", {"c": "/a"}) == (JoinStep("fact", "c_id", "c", "id"),)


def test_safe_path_is_used_when_a_shorter_path_would_fan_out():
    cat = catalog(["fact", "x", "y"],
                  [("fact", "id", "x", "fact_id", "one_to_many"),
                   ("fact", "y_id", "y", "id", "many_to_one"), ("y", "x_id", "x", "id", "many_to_one")])
    assert plan_joins(cat, "fact", {"x": "/a"}) == (JoinStep("fact", "y_id", "y", "id"),
                                                     JoinStep("y", "x_id", "x", "id"))
