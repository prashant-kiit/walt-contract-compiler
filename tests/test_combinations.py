"""S10: every row of the DESIGN.md §6.7 feature-interaction table, on the synthetic model.

✅ rows execute on DuckDB against fixtures/synthetic/seed.sql with hand-computed results
(joined data tabulated in tests/synthetic_helpers.py). ❌ rows assert the documented error code and path.

Trips on the two compare days used throughout (2026-01-07 = T07, T08 is always outside the periods):
  2026-01-05: T01 Harbor  elecF NL member  r101 fare 3    km 2.5
              T02 Harbor  elecT NL !member r102 fare 5    km 6.0
              T03 Smith's elecT DE member  r101 fare 4    km 4.0
  2026-01-06: T04 Smith's elecF NL member  r103 fare NULL km 1.5
              T05 Smith's elecT NL !member r102 fare 6    km 6.0
              T06 Harbor  elecF -- !member NULL fare 2    km 1.0
"""
import copy

import pytest

from walt_compiler.errors import ConflictingFilter, InvalidCompare, InvalidContract, MultipleFactTables
from tests.synthetic_helpers import (ALL_METRICS, CONTRACT_EXPECTED, assert_table, compile_synth,  # noqa: F401
                                     execute, fails, synth_duckdb, synthetic_contract)

D5, D6 = "2026-01-05", "2026-01-06"


def days(primary=D6, outputs=("values", "delta", "pct_change")):
    return {"dimension": "trip_date", "periods": [D5, D6], "primary": primary, "outputs": list(outputs)}


def compare_cols(out, periods=(D5, D6), outputs=("values", "delta", "pct_change")):
    cols = []
    if "values" in outputs:
        cols += [f"{out}_{p}" for p in periods]
    if "delta" in outputs:
        cols.append(f"{out}_delta")
    if "pct_change" in outputs:
        cols.append(f"{out}_pct_change")
    return cols


# === ✅ compare + per-metric filters: period condition AND metric filter ======================

def test_compare_with_per_metric_filters(synth_duckdb):
    # member_fare (member = true): 05 T01 3 + T03 4 = 7; 06 only T04 whose fare is NULL -> NULL; delta, pct NULL
    # nl_fare (maker_country = NL, 3 hops): 05 T01 3 + T02 5 = 8; 06 T04 NULL + T05 6 = 6
    #   delta 6 - 8 = -2; pct 100 * -2 / 8 = -25
    #   (period alone would give 06 = 8, filter alone 14: 6 proves the AND)
    c = {"metrics": [
            {"name": "fare_total", "as": "member_fare", "filters": [{"field": "member", "op": "=", "value": True}]},
            {"name": "fare_total", "as": "nl_fare",
             "filters": [{"field": "maker_country", "op": "=", "value": "NL"}]}],
         "compare": days()}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, compare_cols("member_fare") + compare_cols("nl_fare"),
                 [[7.0, None, None, None, 8.0, 6.0, -2.0, -25.0]])


def test_compare_with_a_filter_on_another_dimension_of_the_same_dataset_is_not_a_conflict(synth_duckdb):
    # start_time is on trips like trip_date, but it is not the compare dimension.
    # start_time < 2026-01-06 12:00: 05 = T01 T02 T03 = 12; 06 = T04 only (T05 is at 12:00, not before) = NULL
    c = {"metrics": [{"name": "fare_total"}], "compare": days(outputs=["values", "delta"]),
         "filters": [{"field": "start_time", "op": "<", "value": "2026-01-06 12:00"}]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, compare_cols("fare_total", outputs=("values", "delta")), [[12.0, None, None]])


# === ✅ compare + totals ======================================================================

def test_compare_with_totals_recomputes_distinct_counts_and_averages(synth_duckdb):
    # Hand-computed table (distinct counts and averages recomputed on the total row) lives in
    # tests/synthetic_helpers.py CONTRACT_EXPECTED, shared with the Postgres suite.
    cols, rows = execute(synth_duckdb, synthetic_contract("riders_by_electric_compare"))
    assert_table(cols, rows, *CONTRACT_EXPECTED["riders_by_electric_compare"])


# === ✅ compare + multiple metrics / multiple group_by ========================================

def test_compare_with_two_metrics_and_two_group_by_columns(synth_duckdb):
    # (district, electric) on 05 / 06, primary 06, delta = 06 - 05:
    #   Harbor  F: T01 (05: 3, 2.5), T06 (06: 2, 1.0) -> fare 3, 2, -1;      km 2.5, 1.0, -1.5
    #   Harbor  T: T02 (05: 5, 6.0) only              -> fare 5, NULL, NULL; km 6.0, NULL, NULL
    #   Smith's F: T04 (06: NULL, 1.5) only           -> fare NULL x3;       km NULL, 1.5, NULL
    #   Smith's T: T03 (05: 4, 4.0), T05 (06: 6, 6.0) -> fare 4, 6, 2;       km 4.0, 6.0, 2.0
    c = {"metrics": [{"name": "fare_total"}, {"name": "longest_km"}], "group_by": ["district", "electric"],
         "compare": days(outputs=["values", "delta"])}
    cols, rows = execute(synth_duckdb, c)
    two = ("values", "delta")
    assert_table(cols, rows,
                 ["district", "electric"] + compare_cols("fare_total", outputs=two)
                 + compare_cols("longest_km", outputs=two),
                 [["Harbor", False, 3.0, 2.0, -1.0, 2.5, 1.0, -1.5],
                  ["Harbor", True, 5.0, None, None, 6.0, None, None],
                  ["Smith's Quay", False, None, None, None, None, 1.5, None],
                  ["Smith's Quay", True, 4.0, 6.0, 2.0, 4.0, 6.0, 2.0]])


def test_compare_over_a_text_dimension_with_a_quote_in_the_period(synth_duckdb):
    # district Harbor vs Smith's Quay (primary), grouped by member; T08 (district NULL) is outside the IN.
    #   member F: Harbor T02 5 + T06 2 = 7 (2 trips); Smith's T05 6 (1 trip) -> count delta -1, fare delta -1
    #   member T: Harbor T01 3 + T07 7 = 10 (2);      Smith's T03 4 + T04 NULL = 4 (2) -> delta 0, -6
    c = {"metrics": [{"name": "trip_count"}, {"name": "fare_total"}], "group_by": ["member"],
         "compare": {"dimension": "district", "periods": ["Harbor", "Smith's Quay"], "primary": "Smith's Quay",
                     "outputs": ["values", "delta"]}}
    cols, rows = execute(synth_duckdb, c)
    p, two = ("Harbor", "Smith's Quay"), ("values", "delta")
    assert_table(cols, rows,
                 ["member"] + compare_cols("trip_count", p, two) + compare_cols("fare_total", p, two),
                 [[False, 2, 1, -1, 7.0, 6.0, -1.0],
                  [True, 2, 2, 0, 10.0, 4.0, -6.0]])


def test_compare_over_an_integer_dimension_two_hops_away(synth_duckdb):
    # launch_year 2023 = T01 (3), T04 (NULL) -> paid_trips 1;  2025 = T02 (5), T05 (6) -> 2
    # pct 100 * (2 - 1) / 1 = 100
    c = {"metrics": [{"name": "paid_trips"}],
         "compare": {"dimension": "launch_year", "periods": ["2023", "2025"], "primary": "2025",
                     "outputs": ["values", "pct_change"]}}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["paid_trips_2023", "paid_trips_2025", "paid_trips_pct_change"], [[1, 2, 100.0]])


def test_compare_over_a_decimal_dimension_names_columns_as_written(synth_duckdb):
    # list_price 899.00 = T01 T04 -> 2 trips; 1499.50 = T02 T03 T05 T08 -> 4; delta 4 - 2 = 2
    c = {"metrics": [{"name": "trip_count"}],
         "compare": {"dimension": "list_price", "periods": ["899.00", "1499.50"], "primary": "1499.50",
                     "outputs": ["values", "delta"]}}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["trip_count_899.00", "trip_count_1499.50", "trip_count_delta"], [[2, 4, 2]])


def test_compare_over_a_boolean_dimension_values(synth_duckdb):
    # electric true (primary): T02 T03 T05 T08 = 18; false: T01 T04 T06 = 3 + 2 = 5; T07 (NULL) excluded
    # delta 18 - 5 = 13; pct 100 * 13 / 5 = 260
    c = {"metrics": [{"name": "fare_total"}],
         "compare": {"dimension": "electric", "periods": [True, False], "primary": True,
                     "outputs": ["values", "delta", "pct_change"]}}
    _, rows = execute(synth_duckdb, c)
    assert len(rows) == 1
    assert [pytest.approx(v) for v in rows[0]] == [18.0, 5.0, 13.0, 260.0]


def test_compare_over_a_boolean_dimension_names_periods_in_json_spelling():
    # "{out}_{period}, where {period} is the period exactly as written in the contract" (§6.3):
    # JSON true / false are written `true` / `false`.
    c = {"metrics": [{"name": "fare_total"}],
         "compare": {"dimension": "electric", "periods": [True, False], "primary": True, "outputs": ["values"]}}
    sql = compile_synth(c)
    assert '"fare_total_true"' in sql and '"fare_total_false"' in sql


# === ✅ compare + any aggregation =============================================================

def test_compare_with_every_aggregation(synth_duckdb):
    # 05 = T01 T02 T03, 06 = T04 T05 T06; primary 06, delta = 06 - 05, pct = 100 * delta / 05
    #   fare_total    3+5+4 = 12      -> NULL+6+2 = 8       delta -4    pct -100/3
    #   trip_count    3               -> 3                  delta 0     pct 0
    #   paid_trips    3               -> 2 (T04 NULL fare)  delta -1    pct -100/3
    #   unique_riders {101,102} = 2   -> {103,102} = 2      delta 0     pct 0
    #   shortest_km   min(2.5,6,4) = 2.5 -> min(1.5,6,1) = 1.0  delta -1.5  pct 100 * -1.5 / 2.5 = -60
    #   longest_km    6.0             -> 6.0 (tie)          delta 0     pct 0
    #   avg_fare      12 / 3 = 4      -> 8 / 2 = 4          delta 0     pct 0
    c = {"metrics": [{"name": n} for n in ALL_METRICS], "compare": days()}
    cols, rows = execute(synth_duckdb, c)
    expected_cols = [col for n in ALL_METRICS for col in compare_cols(n)]
    assert_table(cols, rows, expected_cols, [[
        12.0, 8.0, -4.0, -100.0 / 3,
        3, 3, 0, 0.0,
        3, 2, -1, -100.0 / 3,
        2, 2, 0, 0.0,
        2.5, 1.0, -1.5, -60.0,
        6.0, 6.0, 0.0, 0.0,
        4.0, 4.0, 0.0, 0.0,
    ]])


def test_primary_first_in_periods_reverses_the_delta(synth_duckdb):
    # primary 05: delta = 12 - 8 = 4; pct = 100 * 4 / 8 = 50. Value columns stay in periods order.
    c = {"metrics": [{"name": "fare_total"}], "compare": days(primary=D5)}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, compare_cols("fare_total"), [[12.0, 8.0, 4.0, 50.0]])


# === ✅ totals + any aggregation / multiple group_by ==========================================

def test_totals_with_every_aggregation_and_two_group_by_columns(synth_duckdb):
    # Hand-computed table ((maker_country, member), NULLs last, total recomputed from base rows) lives in
    # tests/synthetic_helpers.py CONTRACT_EXPECTED, shared with the Postgres suite.
    cols, rows = execute(synth_duckdb, synthetic_contract("every_aggregation_by_country_member"))
    assert_table(cols, rows, *CONTRACT_EXPECTED["every_aggregation_by_country_member"])


def test_totals_min_max_avg_over_a_one_hop_boolean(synth_duckdb):
    #   F: T01 T04 T06 km 2.5 1.5 1.0; fares 3 NULL 2 -> min 1.0 max 2.5 avg 5 / 2 = 2.5
    #   T: T02 T03 T05 T08 km 6 4 6 3; fares 5 4 6 3  -> min 3.0 max 6.0 avg 18 / 4 = 4.5
    #   NULL: T07 km 8; fare 7                        -> 8.0, 8.0, 7.0
    #   total: 1.0, 8.0, 30 / 7 (not (2.5 + 4.5 + 7) / 3)
    c = {"metrics": [{"name": "shortest_km"}, {"name": "longest_km"}, {"name": "avg_fare"}],
         "group_by": ["electric"], "totals": "grand"}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["electric", "shortest_km", "longest_km", "avg_fare", "is_total"],
                 [[False, 1.0, 2.5, 2.5, False], [True, 3.0, 6.0, 4.5, False], [None, 8.0, 8.0, 7.0, False],
                  [None, 1.0, 8.0, 30.0 / 7, True]])


def test_totals_with_per_metric_filters(synth_duckdb):
    #   Harbor:  all T01 T02 T06 T07 = 17;  member T01 3 + T07 7 = 10
    #   Smith's: all T03 T04 T05 = 10;      member T03 4 + T04 NULL = 4
    #   NULL:    all T08 = 3;               member none (T08 is not a member) -> NULL
    #   total:   30;                        member 3 + 4 + 7 = 14
    c = {"metrics": [{"name": "fare_total"},
                     {"name": "fare_total", "as": "member_fare",
                      "filters": [{"field": "member", "op": "=", "value": True}]}],
         "group_by": ["district"], "totals": "grand"}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["district", "fare_total", "member_fare", "is_total"],
                 [["Harbor", 17.0, 10.0, False], ["Smith's Quay", 10.0, 4.0, False], [None, 3.0, None, False],
                  [None, 30.0, 14.0, True]])


# === ✅ same metric repeated with different aliases / filters =================================

def test_same_metric_repeated_with_aliases_and_filters(synth_duckdb):
    # Hand-computed table (plain / electric / Harbor / NL AND member fares per trip_date) lives in
    # tests/synthetic_helpers.py CONTRACT_EXPECTED, shared with the Postgres suite.
    cols, rows = execute(synth_duckdb, synthetic_contract("fare_variants_by_day"))
    assert_table(cols, rows, *CONTRACT_EXPECTED["fare_variants_by_day"])


def test_contract_filter_and_per_metric_filter_combine_as_and(synth_duckdb):
    # WHERE district = Harbor: T01 3, T02 5, T06 2, T07 7 = 17; FILTER electric = false: T01 3 + T06 2 = 5
    c = {"metrics": [{"name": "fare_total"},
                     {"name": "fare_total", "as": "plain_harbor",
                      "filters": [{"field": "electric", "op": "=", "value": False}]}],
         "filters": [{"field": "district", "op": "=", "value": "Harbor"}]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["fare_total", "plain_harbor"], [[17.0, 5.0]])


def test_contradictory_filters_are_not_detected_and_yield_null(synth_duckdb):
    # §6.2: WHERE district = Harbor AND FILTER district = Smith's Quay matches nothing -> SUM is NULL
    c = {"metrics": [{"name": "fare_total"},
                     {"name": "fare_total", "as": "never",
                      "filters": [{"field": "district", "op": "=", "value": "Smith's Quay"}]}],
         "filters": [{"field": "district", "op": "=", "value": "Harbor"}]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["fare_total", "never"], [[17.0, None]])


# === ❌ filter on the compare dimension -> ConflictingFilter ==================================

def test_contract_filter_on_the_compare_dimension_conflicts():
    c = {"metrics": [{"name": "fare_total"}], "compare": days(),
         "filters": [{"field": "member", "op": "=", "value": True},
                     {"field": "trip_date", "op": "=", "value": D5}]}
    assert fails(c, ConflictingFilter).path == "/filters/1/field"


def test_contract_in_filter_on_the_compare_dimension_conflicts_even_if_it_covers_both_periods():
    c = {"metrics": [{"name": "fare_total"}], "compare": days(),
         "filters": [{"field": "trip_date", "op": "in", "value": [D5, D6]}]}
    assert fails(c, ConflictingFilter).path == "/filters/0/field"


def test_per_metric_filter_on_the_compare_dimension_conflicts():
    c = {"metrics": [{"name": "trip_count"},
                     {"name": "fare_total", "filters": [{"field": "electric", "op": "=", "value": True},
                                                        {"field": "trip_date", "op": ">", "value": D5}]}],
         "compare": days()}
    assert fails(c, ConflictingFilter).path == "/metrics/1/filters/1/field"


def test_filter_on_a_multi_hop_compare_dimension_conflicts():
    c = {"metrics": [{"name": "fare_total"}], "group_by": ["district"], "totals": "grand",
         "compare": {"dimension": "maker_country", "periods": ["NL", "DE"], "primary": "NL", "outputs": ["values"]},
         "filters": [{"field": "maker_country", "op": "!=", "value": "FR"}]}
    assert fails(c, ConflictingFilter).path == "/filters/0/field"


# === ❌ compare dimension also in group_by -> InvalidCompare ==================================

@pytest.mark.parametrize("group_by", [["trip_date"], ["district", "trip_date"]])
def test_compare_dimension_in_group_by(group_by):
    c = {"metrics": [{"name": "fare_total"}], "group_by": group_by, "compare": days()}
    assert fails(c, InvalidCompare).path == "/compare/dimension"


def test_compare_dimension_in_group_by_with_totals():
    c = {"metrics": [{"name": "fare_total"}], "group_by": ["electric"], "totals": "grand",
         "compare": {"dimension": "electric", "periods": [True, False], "primary": True, "outputs": ["values"]}}
    assert fails(c, InvalidCompare).path == "/compare/dimension"


# === ❌ totals with empty group_by -> InvalidContract =========================================

@pytest.mark.parametrize("extra", [
    {"group_by": []},
    {},
    {"group_by": [], "compare": days()},
])
def test_totals_without_group_by(extra):
    c = {"metrics": [{"name": "fare_total"}], "totals": "grand", **copy.deepcopy(extra)}
    assert fails(c, InvalidContract).path == "/totals"


# === ❌ metrics from different datasets -> MultipleFactTables =================================

@pytest.mark.parametrize("metrics", [
    [{"name": "trip_count"}, {"name": "audit_count"}],
    [{"name": "audit_count"}, {"name": "trip_count"}],
    [{"name": "trip_count"}, {"name": "audit_count", "as": "audits"}],
])
def test_metrics_from_different_datasets(metrics):
    assert fails({"metrics": metrics}, MultipleFactTables).path == "/metrics/1/name"


def test_metrics_from_different_datasets_inside_a_compare():
    c = {"metrics": [{"name": "fare_total"}, {"name": "unique_riders"}, {"name": "audit_count"}],
         "compare": days()}
    assert fails(c, MultipleFactTables).path == "/metrics/2/name"
