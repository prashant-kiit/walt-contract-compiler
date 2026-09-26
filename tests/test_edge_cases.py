"""S10: edge cases DESIGN.md defines, on the synthetic model (§5, §6.1, §6.3-§6.5, §9).

Joined data is tabulated in tests/synthetic_helpers.py; every expectation is computed by hand from it.
Division by zero is deliberately absent: DESIGN.md pins DuckDB's native result in S11, not here.
"""
import datetime as dt

import pytest

from walt_compiler.errors import (DuplicateOutputName, InvalidCompare, InvalidLiteral, NoJoinPath,
                                  UnsupportedRelationship)
from tests.synthetic_helpers import (assert_table, compile_synth, execute, fails,  # noqa: F401 (fixture)
                                     synth_duckdb)

D5, D6, D7 = dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 7)
NO_TRIPS = {"field": "trip_date", "op": "=", "value": "2026-02-01"}     # no trip on that day


# --- empty result sets -----------------------------------------------------------------------

def test_empty_grouped_result_has_the_columns_and_no_rows(synth_duckdb):
    c = {"metrics": [{"name": "trip_count"}, {"name": "fare_total"}], "group_by": ["district"],
         "filters": [NO_TRIPS]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["district", "trip_count", "fare_total"], [])


def test_empty_ungrouped_result_is_one_row(synth_duckdb):
    # §6.5: without group_by the result is one row. Over no rows COUNT is 0 and SUM is NULL (standard SQL).
    c = {"metrics": [{"name": "trip_count"}, {"name": "fare_total"}], "filters": [NO_TRIPS]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["trip_count", "fare_total"], [[0, None]])


def test_empty_result_with_totals_is_only_the_total_row(synth_duckdb):
    # The () grouping set is the aggregate over zero base rows: one row, COUNT 0, SUM NULL, is_total true.
    c = {"metrics": [{"name": "trip_count"}, {"name": "fare_total"}], "group_by": ["district"],
         "totals": "grand", "filters": [NO_TRIPS]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["district", "trip_count", "fare_total", "is_total"], [[None, 0, None, True]])


def test_compare_periods_with_no_data_are_null_not_zero(synth_duckdb):
    # §6.3: no rows for a period -> NULL, not 0. Neither day has trips, so both sums, delta and pct are NULL.
    c = {"metrics": [{"name": "fare_total"}],
         "compare": {"dimension": "trip_date", "periods": ["2025-12-31", "2026-02-01"], "primary": "2026-02-01",
                     "outputs": ["values", "delta", "pct_change"]}}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["fare_total_2025-12-31", "fare_total_2026-02-01", "fare_total_delta",
                              "fare_total_pct_change"], [[None, None, None, None]])


def test_one_period_missing_in_a_group_is_null(synth_duckdb):
    # maker_country, 05 vs 07 (primary 07):
    #   DE: 05 T03 4;             07 T08 3  -> delta -1, pct 100 * -1 / 4 = -25
    #   NL: 05 T01 3 + T02 5 = 8; 07 none   -> NULL, delta NULL, pct NULL
    #   --: 05 none;              07 T07 7  -> NULL, 7, NULL, NULL
    c = {"metrics": [{"name": "fare_total"}], "group_by": ["maker_country"],
         "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-07"], "primary": "2026-01-07",
                     "outputs": ["values", "delta", "pct_change"]}}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["maker_country", "fare_total_2026-01-05", "fare_total_2026-01-07",
                              "fare_total_delta", "fare_total_pct_change"],
                 [["DE", 4.0, 3.0, -1.0, -25.0], ["NL", 8.0, None, None, None], [None, None, 7.0, None, None]])


# --- NULL group keys and NULL aggregates ---------------------------------------------------

def test_null_group_key_is_distinct_from_the_total_row(synth_duckdb):
    # maker_country: DE T03 T08 = 2; NL T01 T02 T04 T05 = 4; NULL (orphans T06 hop 2, T07 hop 1) = 2; total 8
    c = {"metrics": [{"name": "trip_count"}], "group_by": ["maker_country"], "totals": "grand"}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["maker_country", "trip_count", "is_total"],
                 [["DE", 2, False], ["NL", 4, False], [None, 2, False], [None, 8, True]])


def test_orphans_at_different_hops_share_the_null_group(synth_duckdb):
    # T06's cycle exists (so electric = false) but its model does not; T07's cycle does not exist at all.
    # Grouped by (electric, launch_year): T06 -> (False, NULL), T07 -> (NULL, NULL)
    #   F 2023: T01 T04 = 2   F NULL: T06 = 1   T 2024: T03 T08 = 2   T 2025: T02 T05 = 2   NULL NULL: T07 = 1
    c = {"metrics": [{"name": "trip_count"}], "group_by": ["electric", "launch_year"]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["electric", "launch_year", "trip_count"],
                 [[False, 2023, 2], [False, None, 1], [True, 2024, 2], [True, 2025, 2], [None, None, 1]])


def test_a_group_whose_only_value_is_null_aggregates_to_null(synth_duckdb):
    # member = true AND launch_year = 2023: T01 (fare 3), T04 (NULL). Grouped by trip_date:
    #   05: T01 -> sum 3, count(fare) 1, avg 3;  06: T04 -> sum NULL, count(fare) 0, avg NULL
    c = {"metrics": [{"name": "fare_total"}, {"name": "paid_trips"}, {"name": "avg_fare"}],
         "group_by": ["trip_date"],
         "filters": [{"field": "member", "op": "=", "value": True},
                     {"field": "launch_year", "op": "=", "value": 2023}]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["trip_date", "fare_total", "paid_trips", "avg_fare"],
                 [[D5, 3.0, 1, 3.0], [D6, None, 0, None]])


# --- ordering ------------------------------------------------------------------------------

def test_order_follows_group_by_order_with_nulls_last(synth_duckdb):
    # (electric, trip_date): F05 T01; F06 T04 T06; T05 T02 T03; T06 T05; T07 T08; NULL07 T07
    c = {"metrics": [{"name": "trip_count"}], "group_by": ["electric", "trip_date"]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["electric", "trip_date", "trip_count"],
                 [[False, D5, 1], [False, D6, 2], [True, D5, 2], [True, D6, 1], [True, D7, 1], [None, D7, 1]])
    assert compile_synth(c).endswith('ORDER BY "electric" ASC NULLS LAST, "trip_date" ASC NULLS LAST')


def test_reversing_group_by_reverses_the_sort_keys(synth_duckdb):
    c = {"metrics": [{"name": "trip_count"}], "group_by": ["trip_date", "electric"]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["trip_date", "electric", "trip_count"],
                 [[D5, False, 1], [D5, True, 2], [D6, False, 2], [D6, True, 1], [D7, True, 1], [D7, None, 1]])
    assert compile_synth(c).endswith('ORDER BY "trip_date" ASC NULLS LAST, "electric" ASC NULLS LAST')


def test_totals_order_puts_is_total_first_without_nulls_last():
    c = {"metrics": [{"name": "trip_count"}], "group_by": ["list_price", "hq_city"], "totals": "grand"}
    assert compile_synth(c).endswith(
        'ORDER BY "is_total" ASC, "list_price" ASC NULLS LAST, "hq_city" ASC NULLS LAST')


def test_decimal_group_keys_sort_numerically_with_the_null_group_last(synth_duckdb):
    # 899.00 (T01 T04), 1499.50 (the CM-B / CM-C tie: T02 T03 T05 T08), NULL (T06 T07), then the total
    c = {"metrics": [{"name": "trip_count"}], "group_by": ["list_price"], "totals": "grand"}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["list_price", "trip_count", "is_total"],
                 [[899.0, 2, False], [1499.5, 4, False], [None, 2, False], [None, 8, True]])


# --- joins: fan-out guard, one_to_one, no reverse walk, filters pull their own joins ---------

@pytest.mark.parametrize("contract", [
    {"metrics": [{"name": "trip_count"}], "group_by": ["auditor"]},
    {"metrics": [{"name": "trip_count"}], "filters": [{"field": "auditor", "op": "=", "value": "kim"}]},
    {"metrics": [{"name": "trip_count", "filters": [{"field": "auditor", "op": "=", "value": "kim"}]}]},
    {"metrics": [{"name": "trip_count"}],
     "compare": {"dimension": "auditor", "periods": ["kim", "lee"], "primary": "kim", "outputs": ["values"]}},
])
def test_a_one_to_many_hop_is_refused(contract):
    # trips -> docks is many_to_one, docks -> dock_audits is one_to_many: it would fan out trip rows.
    fails(contract, UnsupportedRelationship)


def test_the_one_to_many_dataset_itself_can_be_the_base(synth_duckdb):
    # audit_count over dock_audits alone: kim A1, lee A2 -> 1 each
    cols, rows = execute(synth_duckdb, {"metrics": [{"name": "audit_count"}], "group_by": ["auditor"]})
    assert_table(cols, rows, ["auditor", "audit_count"], [["kim", 1], ["lee", 1]])


def test_relationships_are_never_walked_backwards():
    # docks -> dock_audits is declared; dock_audits -> docks is not, and is not inferred.
    fails({"metrics": [{"name": "audit_count"}], "group_by": ["district"]}, NoJoinPath)


def test_one_to_one_hop_is_followed(synth_duckdb):
    # hq_city via makers -one_to_one-> maker_hq, filtered: Utrecht = MK1 = T01 T02 T04 T05 -> 4 trips
    c = {"metrics": [{"name": "trip_count"}], "filters": [{"field": "hq_city", "op": "=", "value": "Utrecht"}]}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["trip_count"], [[4]])


def test_a_per_metric_filter_pulls_in_only_its_own_join_chain():
    sql = compile_synth({"metrics": [{"name": "fare_total"},
                                     {"name": "fare_total", "as": "nl_fare",
                                      "filters": [{"field": "maker_country", "op": "=", "value": "NL"}]}]})
    for t in ["cycles", "cycle_models", "makers"]:
        assert f'LEFT JOIN "{t}"' in sql
    for t in ["maker_hq", "docks", "dock_audits"]:
        assert f'"{t}"' not in sql
    assert sql.count("LEFT JOIN") == 3


def test_a_dataset_needed_twice_is_joined_once():
    # cycles is needed by the group key (electric), a contract filter (frame_kg) and a per-metric filter
    # reached through it (launch_year); every dataset is joined at most once (§6.6).
    c = {"metrics": [{"name": "fare_total", "as": "new_fare",
                      "filters": [{"field": "launch_year", "op": ">=", "value": 2024}]}],
         "group_by": ["electric"], "filters": [{"field": "frame_kg", "op": ">", "value": 10}]}
    sql = compile_synth(c)
    assert sql.count('LEFT JOIN "cycles"') == 1
    assert sql.count('LEFT JOIN "cycle_models"') == 1
    assert sql.count("LEFT JOIN") == 2


# --- compare details ------------------------------------------------------------------------

def test_compare_outputs_follow_values_delta_pct_order_regardless_of_listing(synth_duckdb):
    # §6.5: each metric's columns are values, then delta, then pct_change. 05 = 12, 06 = 8, pct -100/3
    c = {"metrics": [{"name": "fare_total"}],
         "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-06"], "primary": "2026-01-06",
                     "outputs": ["pct_change", "values"]}}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["fare_total_2026-01-05", "fare_total_2026-01-06", "fare_total_pct_change"],
                 [[12.0, 8.0, -100.0 / 3]])


def test_compare_delta_only(synth_duckdb):
    # 8 - 12 = -4
    c = {"metrics": [{"name": "fare_total"}],
         "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-06"], "primary": "2026-01-06",
                     "outputs": ["delta"]}}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["fare_total_delta"], [[-4.0]])


@pytest.mark.parametrize("compare, path", [
    ({"dimension": "trip_date", "periods": ["2026-1-5", "2026-01-06"], "primary": "2026-01-06"},
     "/compare/periods/0"),
    ({"dimension": "electric", "periods": [True, "false"], "primary": True}, "/compare/periods/1"),
    ({"dimension": "start_time", "periods": ["2026-01-05", "2026-01-06 00:00"], "primary": "2026-01-06 00:00"},
     "/compare/periods/0"),
])
def test_compare_periods_are_coerced_strictly(compare, path):
    c = {"metrics": [{"name": "fare_total"}], "compare": {**compare, "outputs": ["values"]}}
    assert fails(c, InvalidLiteral).path == path


def test_compare_periods_equal_after_decimal_coercion():
    # "1499.5" and "1499.50" are the same decimal value
    c = {"metrics": [{"name": "trip_count"}],
         "compare": {"dimension": "list_price", "periods": ["1499.5", "1499.50"], "primary": "1499.5",
                     "outputs": ["values"]}}
    assert fails(c, InvalidCompare).path == "/compare/periods/1"


def test_a_period_value_clashing_with_a_generated_compare_name():
    # A text period spelled "delta" makes the value column fare_total_delta, the same name as the delta column.
    c = {"metrics": [{"name": "fare_total"}],
         "compare": {"dimension": "district", "periods": ["Harbor", "delta"], "primary": "Harbor",
                     "outputs": ["values", "delta"]}}
    fails(c, DuplicateOutputName)


def test_an_aliased_metric_in_a_compare_is_expanded_too(synth_duckdb):
    # The alias is the {out} stem, so "fare_total_delta" as an alias yields fare_total_delta_<period> columns
    # and no clash. trip_count: 05 = 3, 06 = 3, delta 0; fare_total: 12, 8, -4.
    c = {"metrics": [{"name": "fare_total"}, {"name": "trip_count", "as": "fare_total_delta"}],
         "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-06"], "primary": "2026-01-06",
                     "outputs": ["values", "delta"]}}
    cols, rows = execute(synth_duckdb, c)
    assert_table(cols, rows, ["fare_total_2026-01-05", "fare_total_2026-01-06", "fare_total_delta",
                              "fare_total_delta_2026-01-05", "fare_total_delta_2026-01-06",
                              "fare_total_delta_delta"],
                 [[12.0, 8.0, -4.0, 3, 3, 0]])


def test_an_alias_clashing_with_a_group_column():
    c = {"metrics": [{"name": "trip_count", "as": "district"}], "group_by": ["district"]}
    assert fails(c, DuplicateOutputName).path == "/metrics/0/as"


def test_missing_compare_period_counts_are_zero_and_other_aggregates_null(synth_duckdb):
    # §6.3 (amended at Gate 1): a group with no rows for a period gives 0 for count / count_distinct
    # and NULL for sum / min / max / avg. Primary 07, delta = 07 - 05.
    #   05: T01 Velo r101 fare 3 km 2.5 | T02 Velo r102 fare 5 km 6.0 | T03 Rad r101 fare 4 km 4.0
    #   07: T07 (no maker) r101 fare 7 km 8.0 | T08 Rad r103 fare 3 km 3.0
    #   Rad:  05 T03 -> 1, 1, 4, 4.0, 4.0, 4.0;  07 T08 -> 1, 1, 3, 3.0, 3.0, 3.0
    #         deltas 0, 0, -1, -1.0, -1.0, -1.0
    #   Velo: 05 T01 T02 -> 2, {101,102} = 2, 3+5 = 8, 2.5, 6.0, 8/2 = 4.0;  07 none -> 0, 0, NULL x4
    #         deltas 0-2 = -2, -2, NULL x4
    #   NULL: 05 none -> 0, 0, NULL x4;  07 T07 -> 1, 1, 7, 8.0, 8.0, 7.0
    #         deltas 1, 1, NULL x4
    metrics = ["trip_count", "unique_riders", "fare_total", "shortest_km", "longest_km", "avg_fare"]
    c = {"metrics": [{"name": n} for n in metrics], "group_by": ["maker_name"],
         "compare": {"dimension": "trip_date", "periods": ["2026-01-05", "2026-01-07"], "primary": "2026-01-07",
                     "outputs": ["values", "delta"]}}
    cols, rows = execute(synth_duckdb, c)
    expected_cols = ["maker_name"] + [f"{n}_{s}" for n in metrics for s in ("2026-01-05", "2026-01-07", "delta")]
    #            trip_count  unique_riders  fare_total          shortest_km         longest_km          avg_fare
    assert_table(cols, rows, expected_cols, [
        ["Rad",  1, 1, 0,    1, 1, 0,       4.0, 3.0, -1.0,     4.0, 3.0, -1.0,     4.0, 3.0, -1.0,     4.0, 3.0, -1.0],
        ["Velo", 2, 0, -2,   2, 0, -2,      8.0, None, None,    2.5, None, None,    6.0, None, None,    4.0, None, None],
        [None,   0, 1, 1,    0, 1, 1,       None, 7.0, None,    None, 8.0, None,    None, 8.0, None,    None, 7.0, None],
    ])
