"""S11: output names longer than the dialect's identifier limit (DESIGN.md §6.5, §8, §9).

Each dialect declares `max_identifier_length` in UTF-8 bytes: Postgres 63, DuckDB None (no limit). An output
name over the limit raises IdentifierTooLong at compile time, because Postgres would otherwise truncate it
silently (and two long names could collide with no DuplicateOutputName).

Path of the error = the source of the name:
  - `/metrics/i/as`        an alias (the contract key is `as`; same path DuplicateOutputName uses)
  - `/metrics/i/name`      an unaliased metric's model name
  - `/compare/periods/j`   a generated `{out}_{period}` column, when `{out}` alone fits the limit
                           (otherwise the alias / name path: the base name is already the problem)
Derived `{out}_delta` / `{out}_pct_change` names involve no period, so they blame the alias / name.

`errors.IdentifierTooLong` is looked up inside each test, so a missing class fails the test, not collection.

The 63-byte round trip on a real Postgres is in test_postgres.py.
"""
import copy

import pytest

from walt_compiler import compile_contract, errors
from walt_compiler.dialects import get_dialect
from tests.conftest import run
from tests.helpers import pdf_model

PG_LIMIT = 63


def nbytes(s):
    return len(s.encode("utf-8"))


def alias_contract(*aliases):
    return {"metrics": [{"name": "total_revenue", "as": a} for a in aliases]}


def channel_compare(periods, primary=None, outputs=("values",), metric=None):
    # channel is a text dimension of the PDF model, so a period can be any string.
    return {"metrics": [metric or {"name": "total_revenue"}],
            "compare": {"dimension": "channel", "periods": list(periods), "primary": primary or periods[0],
                        "outputs": list(outputs)}}


def model_with_metric(name):
    m = copy.deepcopy(pdf_model())
    m["metrics"].append({"name": name, "agg": "sum", "expression": "revenue", "model": "fact_sales",
                         "measure_class": "additive"})
    return m


def too_long(contract, model=None):
    with pytest.raises(errors.IdentifierTooLong) as exc:
        compile_contract(model or pdf_model(), contract, "postgres")
    assert exc.value.code == "IdentifierTooLong"
    return exc.value


# === the dialect attribute ===================================================================

def test_postgres_limit_is_63_bytes():
    assert get_dialect("postgres").max_identifier_length == 63


def test_duckdb_has_no_limit():
    assert get_dialect("duckdb").max_identifier_length is None


# === boundary: bytes, not characters =========================================================

@pytest.mark.parametrize("alias", ["a" * 63, "ü" * 31 + "a"], ids=["ascii-63", "utf8-63-bytes"])
def test_exactly_63_bytes_compiles_on_postgres(alias):
    assert nbytes(alias) == 63
    sql = compile_contract(pdf_model(), alias_contract(alias), "postgres")
    assert f'AS "{alias}"' in sql


@pytest.mark.parametrize("alias", ["a" * 64, "ü" * 32, "ü" * 31 + "ab"],
                         ids=["ascii-64", "utf8-32-chars-64-bytes", "utf8-33-chars-65-bytes"])
def test_over_63_bytes_raises_on_postgres(alias):
    assert nbytes(alias) > PG_LIMIT and len(alias) <= 64
    assert too_long(alias_contract(alias)).path == "/metrics/0/as"


def test_multibyte_name_under_63_characters_but_over_63_bytes_raises():
    alias = "ü" * 40                       # 40 characters, 80 bytes
    assert len(alias) <= PG_LIMIT < nbytes(alias)
    assert too_long(alias_contract(alias)).path == "/metrics/0/as"


@pytest.mark.parametrize("contract, model", [
    (alias_contract("a" * 64), None),
    (alias_contract("ü" * 40), None),
    ({"metrics": [{"name": "m" * 64}]}, model_with_metric("m" * 64)),
    (channel_compare(["Online", "x" * 50]), None),
], ids=["alias", "utf8-alias", "metric-name", "period"])
def test_the_same_contract_compiles_on_duckdb(contract, model):
    assert compile_contract(model or pdf_model(), contract, "duckdb")


# === path = the source of the name ===========================================================

def test_alias_path_uses_the_metric_index():
    assert too_long(alias_contract("ok", "a" * 64)).path == "/metrics/1/as"


def test_unaliased_metric_name_path():
    name = "m" * 64
    assert too_long({"metrics": [{"name": "total_revenue"}, {"name": name}]}, model_with_metric(name)).path \
        == "/metrics/1/name"


def test_period_path_when_the_base_name_fits():
    # "total_revenue_" is 14 bytes: + 49-byte period = 63 (fine), + 50-byte period = 64 (too long).
    assert nbytes("total_revenue_" + "x" * 49) == 63
    assert compile_contract(pdf_model(), channel_compare(["Online", "x" * 49]), "postgres")
    assert too_long(channel_compare(["Online", "x" * 50])).path == "/compare/periods/1"
    assert too_long(channel_compare(["x" * 50, "Online"])).path == "/compare/periods/0"


def test_period_path_even_when_a_short_period_pushes_a_long_alias_over():
    # alias 60 bytes fits alone; "{alias}_Online" is 67 bytes -> the period is blamed (ruling: base fits).
    alias = "a" * 60
    c = channel_compare(["Online", "Retail"], metric={"name": "total_revenue", "as": alias})
    assert too_long(c).path == "/compare/periods/0"


def test_alias_path_when_the_base_name_alone_is_too_long_under_compare():
    c = channel_compare(["Online", "Retail"], metric={"name": "total_revenue", "as": "a" * 64})
    assert too_long(c).path == "/metrics/0/as"


def test_metric_name_path_when_the_base_name_alone_is_too_long_under_compare():
    name = "m" * 64
    c = channel_compare(["Online", "Retail"], metric={"name": name})
    assert too_long(c, model_with_metric(name)).path == "/metrics/0/name"


@pytest.mark.parametrize("outputs, suffix", [(("delta",), "_delta"), (("pct_change",), "_pct_change")])
def test_derived_suffix_pushing_the_name_over_blames_the_alias(outputs, suffix):
    # Only the derived column is too long. The per-period names "{alias}_A" / "{alias}_B" (also the inner
    # measure aliases) fit, and the derived name has no period in it. Periods "A"/"B" match no row; the
    # SQL is only compiled.
    alias = "a" * (PG_LIMIT + 1 - len(suffix))       # alias + suffix = 64 bytes
    assert nbytes(alias + suffix) == 64 and nbytes(alias + "_A") <= PG_LIMIT
    c = channel_compare(["A", "B"], outputs=outputs, metric={"name": "total_revenue", "as": alias})
    assert too_long(c).path == "/metrics/0/as"


# === the collision truncation would cause ====================================================

def test_names_equal_in_their_first_63_bytes_raise_instead_of_colliding(pdf_duckdb):
    first, second = "x" * 63 + "_a", "x" * 63 + "_b"
    assert first.encode()[:63] == second.encode()[:63]
    assert too_long(alias_contract(first, second)).path == "/metrics/0/as"
    # DuckDB has no limit: both names survive intact and distinct.
    cols, _ = run(pdf_duckdb, compile_contract(pdf_model(), alias_contract(first, second), "duckdb"))
    assert cols == [first, second]


# === group_by dimension names are output names too ============================================

def synthetic_with_dimension_renamed(new_name):
    # maker_name declares `column: maker_label`, so renaming the public name leaves the physical column alone.
    from tests.synthetic_helpers import synthetic_model
    m = synthetic_model()
    (dim,) = [d for d in m["dimensions"] if d["name"] == "maker_name"]
    assert dim["column"] == "maker_label"
    dim["name"] = new_name
    return m


def group_by_contract(dimension):
    return {"metrics": [{"name": "trip_count"}], "group_by": ["member", dimension]}


def test_group_by_dimension_name_over_63_bytes_raises_on_postgres():
    name = "d" * 64
    model = synthetic_with_dimension_renamed(name)
    assert too_long(group_by_contract(name), model).path == "/group_by/1"
    assert compile_contract(model, group_by_contract(name), "duckdb")


def test_group_by_dimension_name_of_63_bytes_compiles_on_postgres():
    name = "d" * 63
    sql = compile_contract(synthetic_with_dimension_renamed(name), group_by_contract(name), "postgres")
    assert f'AS "{name}"' in sql


# === inner per-period measure aliases are generated identifiers too (DESIGN.md §8) =============
# With outputs ["delta"] the `{out}_{period}` names are never projected, but they are still the inner
# measure aliases, which Postgres would truncate just the same.

def test_delta_only_inner_period_alias_over_63_bytes_raises_at_the_period():
    # "total_revenue_" is 14 bytes + 50-byte period = 64; "total_revenue_delta" (19 bytes) fits.
    period = "x" * 50
    assert nbytes("total_revenue_" + period) == 64
    assert too_long(channel_compare(["Online", period], outputs=["delta"])).path == "/compare/periods/1"
    assert too_long(channel_compare([period, "Online"], outputs=["delta"])).path == "/compare/periods/0"


def test_delta_only_inner_period_alias_of_63_bytes_compiles_on_postgres():
    period = "x" * 49
    assert compile_contract(pdf_model(), channel_compare(["Online", period], outputs=["delta"]), "postgres")


def test_delta_only_long_alias_itself_blames_the_alias():
    # {out} alone is over the limit: the alias / name path, not the period (same base-name rule).
    c = channel_compare(["Online", "Retail"], outputs=["delta"], metric={"name": "total_revenue", "as": "a" * 64})
    assert too_long(c).path == "/metrics/0/as"


def test_delta_only_period_aliases_that_would_collide_after_truncation(pdf_duckdb):
    # Both inner aliases are 76 bytes and share their first 63: truncated, they would be the same name.
    p1, p2 = "p" * 60 + "_1", "p" * 60 + "_2"
    a1, a2 = ("total_revenue_" + p1).encode(), ("total_revenue_" + p2).encode()
    assert a1 != a2 and a1[:63] == a2[:63]
    c = channel_compare([p1, p2], primary=p2, outputs=["delta"])
    assert too_long(c).path == "/compare/periods/0"
    # DuckDB has no limit: it compiles and runs (no row has these channels, so the delta is NULL).
    cols, rows = run(pdf_duckdb, compile_contract(pdf_model(), c, "duckdb"))
    assert cols == ["total_revenue_delta"]
    assert rows == [(None,)]


# === the message names the dialect and its limit ==============================================

@pytest.mark.parametrize("contract", [
    alias_contract("a" * 64),
    channel_compare(["Online", "x" * 50], outputs=["delta"]),
], ids=["output-alias", "inner-period-alias"])
def test_message_names_the_dialect_and_the_limit(contract):
    message = str(too_long(contract))
    assert "postgres" in message
    assert "63" in message
