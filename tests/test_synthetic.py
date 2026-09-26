"""S10: the synthetic bike-share model proves generality (DESIGN.md §2, §5, §6.1, §10).

A second model that shares no names with the PDF's; a 4-hop snowflake (trips -> cycles -> cycle_models
-> makers -one_to_one-> maker_hq); all seven dimension types; all six aggregations; dimensions whose
public name differs from the physical column. The joined data and grand aggregates are tabulated in
tests/synthetic_helpers.py; every expectation below is computed by hand from that table.
"""
import datetime as dt
import json
import re

import pytest

from walt_compiler.errors import (InvalidLiteral, ModelMismatch, UnknownDimension, UnknownMetric,
                                  UnsupportedOperator)
from tests.synthetic_helpers import (ALL_METRICS, SYNTHETIC, assert_table, compile_synth, execute, fails,
                                     pdf_model_raw, synth_duckdb, synthetic_model)  # noqa: F401 (fixture)

D5, D6, D7 = dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 7)
ALL_DATASETS = ["trips", "cycles", "cycle_models", "makers", "maker_hq", "docks", "dock_audits"]


def by(dimension, **extra):
    return {"metrics": [{"name": "trip_count"}, {"name": "fare_total"}], "group_by": [dimension], **extra}


# --- the model really is a different one -----------------------------------------------------

def _names(model):
    names = set()
    for d in model["datasets"]:
        names.add(d["name"])
    for r in model["relationships"]:
        names |= {r["from_column"], r["to_column"]}
    for m in model["metrics"]:
        names |= {m["name"], m["expression"]}
    for d in model["dimensions"]:
        names |= {d["name"], d.get("column", d["name"])}
    return names


def test_synthetic_model_shares_no_name_with_the_pdf_model():
    assert _names(synthetic_model()) & _names(pdf_model_raw()) == set()


def test_synthetic_model_covers_every_type_and_aggregation():
    m = synthetic_model()
    assert {d["type"] for d in m["dimensions"]} == {"text", "integer", "double", "decimal", "date", "timestamp",
                                                   "boolean"}
    assert {x["agg"] for x in m["metrics"]} == {"sum", "count", "count_distinct", "min", "max", "avg"}
    assert any(d.get("column", d["name"]) != d["name"] for d in m["dimensions"])


# --- every aggregation ----------------------------------------------------------------------

def test_every_aggregation_ungrouped(synth_duckdb):
    # fare_total 3+5+4+6+2+7+3 = 30 (T04 NULL); trip_count 8; paid_trips = count(fare) = 7 (T04 NULL);
    # unique_riders {101,102,103} = 3 (T06 NULL ignored); shortest 1.0 (T06); longest 8.0 (T07); avg 30 / 7
    cols, rows = execute(synth_duckdb, {"metrics": [{"name": n} for n in ALL_METRICS]})
    assert_table(cols, rows, ALL_METRICS, [[30.0, 8, 7, 3, 1.0, 8.0, 30.0 / 7]])


def test_ungrouped_query_has_no_order_by():
    assert "ORDER BY" not in compile_synth({"metrics": [{"name": "fare_total"}]})


# --- group by every dimension type (and every hop) -------------------------------------------

GROUPED = {
    # date on the fact (column trip_day): 05 = T01..T03 (3+5+4), 06 = T04..T06 (NULL+6+2), 07 = T07, T08 (7+3)
    "trip_date": [[D5, 3, 12.0], [D6, 3, 8.0], [D7, 2, 10.0]],
    # timestamp on the fact (column started_at): one trip each, chronological = T01..T08; T04's lone fare is NULL
    "start_time": [[dt.datetime(2026, 1, 5, 8, 0), 1, 3.0], [dt.datetime(2026, 1, 5, 9, 30), 1, 5.0],
                   [dt.datetime(2026, 1, 5, 18, 15), 1, 4.0], [dt.datetime(2026, 1, 6, 7, 45), 1, None],
                   [dt.datetime(2026, 1, 6, 12, 0), 1, 6.0], [dt.datetime(2026, 1, 6, 20, 0), 1, 2.0],
                   [dt.datetime(2026, 1, 7, 10, 0), 1, 7.0], [dt.datetime(2026, 1, 7, 11, 11, 11), 1, 3.0]],
    # boolean on the fact (column is_member): F = T02 T05 T06 T08 (5+6+2+3); T = T01 T03 T04 T07 (3+4+7)
    "member": [[False, 4, 16.0], [True, 4, 14.0]],
    # boolean, 1 hop (column is_electric): F = T01 T04 T06 (3+2); T = T02 T03 T05 T08 (5+4+6+3); NULL = T07
    "electric": [[False, 3, 5.0], [True, 4, 18.0], [None, 1, 7.0]],
    # double, 1 hop: 12.0 = T06; 14.5 = T01 T04; 22.0 = T02 T03 T05 T08 (tie C2/C3); NULL = T07
    "frame_kg": [[12.0, 1, 2.0], [14.5, 2, 3.0], [22.0, 4, 18.0], [None, 1, 7.0]],
    # integer, 2 hops: 2023 = T01 T04; 2024 = T03 T08 (4+3); 2025 = T02 T05 (5+6); NULL = T06 (CM-X) T07 (C9)
    "launch_year": [[2023, 2, 3.0], [2024, 2, 7.0], [2025, 2, 11.0], [None, 2, 9.0]],
    # decimal, 2 hops: 899.00 = T01 T04; 1499.50 = CM-B + CM-C (tie) = T02 T03 T05 T08; NULL = T06 T07
    "list_price": [[899.0, 2, 3.0], [1499.5, 4, 18.0], [None, 2, 9.0]],
    # text, 3 hops (column maker_label): Rad = T03 T08; Velo = T01 T02 T04 T05 (3+5+6); NULL = T06 T07
    "maker_name": [["Rad", 2, 7.0], ["Velo", 4, 14.0], [None, 2, 9.0]],
    # text, 3 hops (column country)
    "maker_country": [["DE", 2, 7.0], ["NL", 4, 14.0], [None, 2, 9.0]],
    # text, 4 hops, the last one one_to_one (followed)
    "hq_city": [["Berlin", 2, 7.0], ["Utrecht", 4, 14.0], [None, 2, 9.0]],
    # text, other branch: Harbor = T01 T02 T06 T07 (3+5+2+7); Smith's Quay = T03 T04 T05 (4+6); NULL = T08 (D9)
    "district": [["Harbor", 4, 17.0], ["Smith's Quay", 3, 10.0], [None, 1, 3.0]],
}


@pytest.mark.parametrize("dimension", list(GROUPED))
def test_group_by_each_dimension(synth_duckdb, dimension):
    cols, rows = execute(synth_duckdb, by(dimension))
    assert_table(cols, rows, [dimension, "trip_count", "fare_total"], GROUPED[dimension])


# --- joins: multi-hop, only what is needed, always LEFT, physical columns ----------------------

JOINED = {
    "trip_date": [], "start_time": [], "member": [],
    "electric": ["cycles"], "frame_kg": ["cycles"],
    "launch_year": ["cycles", "cycle_models"], "list_price": ["cycles", "cycle_models"],
    "maker_name": ["cycles", "cycle_models", "makers"], "maker_country": ["cycles", "cycle_models", "makers"],
    "hq_city": ["cycles", "cycle_models", "makers", "maker_hq"],
    "district": ["docks"],
}

PHYSICAL = {
    "trip_date": ("trips", "trip_day"), "start_time": ("trips", "started_at"), "member": ("trips", "is_member"),
    "electric": ("cycles", "is_electric"), "frame_kg": ("cycles", "frame_kg"),
    "launch_year": ("cycle_models", "launch_year"), "list_price": ("cycle_models", "list_price"),
    "maker_name": ("makers", "maker_label"), "maker_country": ("makers", "country"),
    "hq_city": ("maker_hq", "hq_city"), "district": ("docks", "district"),
}


@pytest.mark.parametrize("dimension", list(JOINED))
def test_only_the_needed_datasets_are_left_joined(dimension):
    sql = compile_synth(by(dimension))
    joined = [t for t in ALL_DATASETS if f'JOIN "{t}"' in sql]
    assert sorted(joined) == sorted(JOINED[dimension])
    assert sql.count("LEFT JOIN") == len(JOINED[dimension])
    assert len(re.findall(r"\bJOIN\b", sql)) == len(JOINED[dimension])      # nothing but LEFT joins
    assert "INNER" not in sql


def test_multi_hop_join_chain_uses_the_declared_keys():
    sql = compile_synth(by("hq_city"))
    for left, right in [('"trips"."cycle_tag"', '"cycles"."tag"'),
                        ('"cycles"."model_ref"', '"cycle_models"."model_ref"'),
                        ('"cycle_models"."maker_key"', '"makers"."maker_key"'),
                        ('"makers"."maker_key"', '"maker_hq"."maker_key"')]:
        assert re.search(re.escape(left) + r"\s*=\s*" + re.escape(right), sql), (left, right)


@pytest.mark.parametrize("dimension", [d for d in PHYSICAL if PHYSICAL[d][1] != d])
def test_column_differs_from_name_uses_the_physical_column(dimension):
    dataset, column = PHYSICAL[dimension]
    sql = compile_synth(by(dimension))
    assert f'"{dataset}"."{column}"' in sql
    assert f'"{dataset}"."{dimension}"' not in sql


def test_metric_expression_column_is_used():
    sql = compile_synth({"metrics": [{"name": "unique_riders"}, {"name": "paid_trips"}]})
    assert '"trips"."rider_ref"' in sql and '"trips"."fare"' in sql
    assert "DISTINCT" in sql


# --- contract filters on every type, with strict literal coercion -----------------------------

FILTERS = [
    # (filter, trip_count, fare_total)
    ({"field": "trip_date", "op": "=", "value": "2026-01-06"}, 3, 8.0),                      # T04 T05 T06
    ({"field": "trip_date", "op": ">=", "value": "2026-01-06"}, 5, 18.0),                    # T04..T08: 6+2+7+3
    ({"field": "trip_date", "op": "in", "value": ["2026-01-05", "2026-01-07"]}, 5, 22.0),    # 12 + 10
    ({"field": "start_time", "op": "<", "value": "2026-01-05T12:00"}, 2, 8.0),               # T01 T02
    ({"field": "start_time", "op": ">", "value": "2026-01-06 07:45"}, 4, 18.0),              # T05..T08 (strict)
    ({"field": "start_time", "op": ">=", "value": "2026-01-07 11:11:11"}, 1, 3.0),           # T08
    ({"field": "start_time", "op": "=", "value": "2026-01-06 20:00:00.000000"}, 1, 2.0),     # T06
    ({"field": "member", "op": "=", "value": False}, 4, 16.0),
    ({"field": "member", "op": "!=", "value": False}, 4, 14.0),
    ({"field": "electric", "op": "=", "value": True}, 4, 18.0),
    ({"field": "electric", "op": "!=", "value": True}, 3, 5.0),                              # T07 NULL excluded
    ({"field": "frame_kg", "op": "<=", "value": 14.5}, 3, 5.0),                              # T01 T04 T06
    ({"field": "frame_kg", "op": ">", "value": 14}, 6, 21.0),                                # 3+5+4+6+3
    ({"field": "launch_year", "op": "in", "value": ["2023", 2025]}, 4, 14.0),                # T01 T04 T02 T05
    ({"field": "launch_year", "op": ">", "value": 2023.0}, 4, 18.0),                         # T02 T03 T05 T08
    ({"field": "launch_year", "op": "!=", "value": 2024}, 4, 14.0),                          # NULLs excluded
    ({"field": "list_price", "op": "=", "value": "1499.50"}, 4, 18.0),
    ({"field": "list_price", "op": "<", "value": 1000}, 2, 3.0),
    ({"field": "list_price", "op": ">=", "value": 899.0}, 6, 21.0),
    ({"field": "maker_country", "op": "!=", "value": "NL"}, 2, 7.0),                         # DE only; NULLs out
    ({"field": "maker_name", "op": "=", "value": "Velo"}, 4, 14.0),
    ({"field": "hq_city", "op": "=", "value": "Berlin"}, 2, 7.0),
    ({"field": "district", "op": "=", "value": "Smith's Quay"}, 3, 10.0),                    # quote escaped
    ({"field": "district", "op": "in", "value": ["Harbor"]}, 4, 17.0),
    ({"field": "district", "op": "!=", "value": "Harbor"}, 3, 10.0),                         # T08 NULL excluded
    ({"field": "district", "op": "=", "value": "Harbor", "model": "docks"}, 4, 17.0),        # explicit model
]


@pytest.mark.parametrize("flt, count, fare", FILTERS, ids=[f"{f['field']}{f['op']}{f['value']}" for f, _, _ in FILTERS])
def test_filter_on_each_type(synth_duckdb, flt, count, fare):
    c = {"metrics": [{"name": "trip_count"}, {"name": "fare_total"}], "filters": [flt]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["trip_count", "fare_total"], [[count, fare]])


def test_several_contract_filters_are_anded(synth_duckdb):
    # Harbor AND not electric: T01 (3), T06 (2); T07 has NULL electric and is excluded
    c = {"metrics": [{"name": "trip_count"}, {"name": "fare_total"}],
         "filters": [{"field": "district", "op": "=", "value": "Harbor"},
                     {"field": "electric", "op": "=", "value": False}]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["trip_count", "fare_total"], [[2, 5.0]])


def test_quote_in_a_text_literal_is_never_pasted_raw():
    sql = compile_synth({"metrics": [{"name": "trip_count"}],
                         "filters": [{"field": "district", "op": "=", "value": "Smith's Quay"}]})
    assert "'Smith''s Quay'" in sql


@pytest.mark.parametrize("field, value", [
    ("trip_date", "20260105"),                 # not YYYY-MM-DD
    ("trip_date", "2026-02-30"),               # not a real day
    ("start_time", "2026-01-05"),              # a date with no time part
    ("start_time", "2026-01-05T08:00:00Z"),    # time zone
    ("member", "true"),                        # booleans are JSON true/false only
    ("electric", 1),
    ("launch_year", 2023.5),
    ("launch_year", " 2023"),
    ("launch_year", True),
    ("list_price", "1_000"),
    ("frame_kg", True),
    ("district", 5),
    ("district", None),
])
def test_invalid_literal_on_the_synthetic_types(field, value):
    err = fails({"metrics": [{"name": "trip_count"}], "filters": [{"field": field, "op": "=", "value": value}]},
                InvalidLiteral)
    assert err.path == "/filters/0/value"


def test_ordering_operator_on_a_boolean_dimension():
    err = fails({"metrics": [{"name": "trip_count"}],
                 "filters": [{"field": "electric", "op": ">=", "value": True}]}, UnsupportedOperator)
    assert err.path == "/filters/0/op"


def test_filter_model_must_be_the_dimensions_dataset():
    # maker_country lives on makers, not on the dataset its join passes through
    err = fails({"metrics": [{"name": "trip_count"}],
                 "filters": [{"field": "maker_country", "op": "=", "value": "NL", "model": "cycle_models"}]},
                ModelMismatch)
    assert err.path == "/filters/0/model"


def test_filter_model_is_the_dataset_not_the_physical_column():
    err = fails({"metrics": [{"name": "trip_count"}],
                 "filters": [{"field": "maker_country", "op": "=", "value": "NL", "model": "country"}]},
                ModelMismatch)
    assert err.path == "/filters/0/model"


# --- names come from the model: typos are suggested, physical names are not public names ------

@pytest.mark.parametrize("contract, error, path, suggestions", [
    ({"metrics": [{"name": "fare_totl"}]}, UnknownMetric, "/metrics/0/name", ("fare_total",)),
    ({"metrics": [{"name": "trip_count"}], "group_by": ["distrct"]}, UnknownDimension, "/group_by/0",
     ("district",)),
    # maker_contry -> maker_country 1/13; maker_name 5/12 = 0.417 > 0.4 is not suggested
    ({"metrics": [{"name": "trip_count"}], "filters": [{"field": "maker_contry", "op": "=", "value": "NL"}]},
     UnknownDimension, "/filters/0/field", ("maker_country",)),
    ({"metrics": [{"name": "trip_count", "filters": [{"field": "distrct", "op": "=", "value": "Harbor"}]}]},
     UnknownDimension, "/metrics/0/filters/0/field", ("district",)),
])
def test_unknown_names_are_suggested_from_the_synthetic_catalog(contract, error, path, suggestions):
    err = fails(contract, error)
    assert err.path == path
    assert err.suggestions == suggestions


@pytest.mark.parametrize("physical", ["country", "trip_day", "is_electric", "maker_label"])
def test_a_physical_column_name_is_not_a_dimension(physical):
    err = fails({"metrics": [{"name": "trip_count"}], "group_by": [physical]}, UnknownDimension)
    assert err.path == "/group_by/0"


def test_a_metric_expression_column_is_not_a_metric():
    fails({"metrics": [{"name": "fare"}]}, UnknownMetric)


# --- determinism on the synthetic model -----------------------------------------------------

def test_synthetic_compile_is_deterministic():
    c = json.loads((SYNTHETIC / "contracts" / "riders_by_electric_compare.json").read_text())
    first = compile_synth(c)
    assert all(compile_synth(c) == first for _ in range(50))
