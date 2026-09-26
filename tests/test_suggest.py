"""S1: Levenshtein-based "did you mean" suggestions (DESIGN.md §9)."""
import pytest

from walt_compiler.suggest import levenshtein, score, suggest


@pytest.mark.parametrize("a, b, dist", [
    ("", "", 0),
    ("abc", "", 3),
    ("", "abc", 3),
    ("abc", "abc", 0),
    ("kitten", "sitting", 3),
    ("flaw", "lawn", 2),
    ("total_revenu", "total_revenue", 1),
])
def test_levenshtein(a, b, dist):
    assert levenshtein(a, b) == dist
    assert levenshtein(b, a) == dist  # symmetric


def test_score_is_distance_over_longer_length():
    assert score("total_revenu", "total_revenue") == pytest.approx(1 / 13)
    assert score("abcde", "abcxy") == pytest.approx(2 / 5)


def test_score_is_case_insensitive():
    assert score("Region", "region") == 0.0


def test_score_of_two_empty_strings_is_zero():
    assert score("", "") == 0.0


def test_suggests_close_name():
    assert suggest("total_revenu", ["total_revenue", "order_count"]) == ("total_revenue",)


def test_distant_name_gets_no_suggestion():
    assert suggest("banana", ["total_revenue", "order_count"]) == ()


def test_empty_key_gets_no_suggestion():
    assert suggest("", ["region", "channel"]) == ()


def test_case_variant_is_suggested_with_original_spelling():
    assert suggest("REGION", ["region", "channel"]) == ("region",)


def test_threshold_boundary_is_inclusive():
    assert suggest("abcde", ["abcxy"], threshold=0.4) == ("abcxy",)
    assert suggest("abcde", ["abcxy"], threshold=0.39) == ()


def test_at_most_limit_sorted_by_score_then_name():
    candidates = ["reg4", "reg3", "region", "reg2", "reg1"]
    # "region" scores 1/6; every "regN" scores 2/5 -> ties broken by name.
    assert suggest("regio", candidates) == ("region", "reg1", "reg2")


def test_order_does_not_depend_on_candidate_order():
    candidates = ["reg4", "reg3", "region", "reg2", "reg1"]
    assert suggest("regio", candidates) == suggest("regio", list(reversed(candidates)))


def test_duplicate_candidates_are_suggested_once():
    assert suggest("regio", ["region", "region"]) == ("region",)


def test_limit_zero_returns_nothing():
    assert suggest("total_revenu", ["total_revenue"], limit=0) == ()


def test_accepts_any_iterable_of_candidates():
    assert suggest("regio", (n for n in ["region"])) == ("region",)
