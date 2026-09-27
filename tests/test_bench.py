"""S13: the compile-time benchmark, bench/bench.py (DESIGN.md §1 "under 10 ms", §10 Performance).

bench/ sits at the repo root next to walt_compiler/ (DESIGN.md §3 layout), is a regular package
(bench/__init__.py, empty) and is imported here as `bench.bench`; pytest already puts the repo
root on sys.path because tests/ is a package.

Interface pinned here (DESIGN.md leaves it open, decided for S13):

  DEFAULT_N = 10_000
  DIALECTS  = ("duckdb", "postgres")

  cases() -> iterable of (label, model, contract), in this order:
      "A", "B", "C"                       fixtures/pdf/contracts/{a,b,c}.json with the PDF model
      "synthetic/<stem>"                  each fixtures/synthetic/contracts/*.json, sorted by file
                                          name, with the synthetic model
  measure(model, contract, dialect, n, *, compile_fn=compile_contract) -> dict
      Times n calls of compile_fn(model, contract, dialect), each one individually, and returns
      exactly {"p50", "p95", "max"} as floats in milliseconds. compile_fn is called exactly n
      times (no hidden warm-up calls through it). n < 1 raises ValueError.
  machine() -> dict
      Exactly {"python", "implementation", "platform", "machine", "processor", "cpus"}, every
      value a non-empty str (processor falls back to something non-empty where
      platform.processor() is "", e.g. on Linux). Stdlib only.
  main(argv) -> int
      `--n N` (default DEFAULT_N). Prints to stdout: a table whose header cells are
      case | dialect | p50_ms | p95_ms | max_ms, then one row per case x dialect (case-major,
      duckdb before postgres), cells joined by "|" (padding free), numbers parseable as floats;
      plus the iteration count as "n=<N>" and one "<key>: <value>" line per machine() entry.
      Returns 0.
  `python -m bench.bench --n N` runs main and exits with its return value.
  bench/bench.py imports only the standard library and walt_compiler (no duckdb, no tests.*).
  The Makefile has a `bench` target (DESIGN.md §3: "make run | make test | make bench").
"""
import ast
import subprocess
import sys

import pytest

from tests.helpers import FIXTURES, PDF, ROOT, load_json
from walt_compiler import compile_contract

try:
    import bench.bench as bench
except ModuleNotFoundError as _exc:  # before S13 lands: every test fails with the import error
    class _Missing:
        def __getattr__(self, name, _exc=_exc):
            raise ModuleNotFoundError(f"bench/bench.py is not importable: {_exc}")

    bench = _Missing()

SYNTHETIC = FIXTURES / "synthetic"
SYNTHETIC_STEMS = sorted(p.stem for p in (SYNTHETIC / "contracts").glob("*.json"))
EXPECTED_LABELS = ["A", "B", "C"] + [f"synthetic/{s}" for s in SYNTHETIC_STEMS]
DIALECTS = ["duckdb", "postgres"]
BUDGET_MS = 10.0
MACHINE_KEYS = {"python", "implementation", "platform", "machine", "processor", "cpus"}


def _cases():
    return list(bench.cases())


# --- cases() -------------------------------------------------------------------------------------

def test_synthetic_fixture_set_is_the_three_contracts():
    # Guard for the label list above: the brief says three synthetic contracts.
    assert SYNTHETIC_STEMS == ["every_aggregation_by_country_member", "fare_variants_by_day",
                               "riders_by_electric_compare"]


def test_cases_are_exactly_a_b_c_and_the_synthetic_contracts_in_order():
    assert [label for label, _, _ in _cases()] == EXPECTED_LABELS


def test_cases_pair_each_contract_with_its_own_model_and_fixture():
    by_label = {label: (model, contract) for label, model, contract in _cases()}
    pdf_model = load_json(PDF / "semantic_model.json")
    synth_model = load_json(SYNTHETIC / "semantic_model.json")
    for name in ("a", "b", "c"):
        assert by_label[name.upper()] == (pdf_model, load_json(PDF / "contracts" / f"{name}.json"))
    for stem in SYNTHETIC_STEMS:
        assert by_label[f"synthetic/{stem}"] == (synth_model,
                                                 load_json(SYNTHETIC / "contracts" / f"{stem}.json"))


@pytest.mark.parametrize("dialect", DIALECTS)
def test_every_case_compiles(dialect):
    for label, model, contract in _cases():
        sql = compile_contract(model, contract, dialect)
        assert isinstance(sql, str) and sql.strip(), label


# --- the budget: p95 < 10 ms per contract per dialect -------------------------------------------

@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("label", EXPECTED_LABELS)
def test_p95_under_10ms(label, dialect):
    model, contract = next((m, c) for lbl, m, c in _cases() if lbl == label)
    stats = bench.measure(model, contract, dialect, 300)
    assert stats["p95"] < BUDGET_MS, f"{label} on {dialect}: p95 {stats['p95']:.3f} ms >= {BUDGET_MS} ms"


# --- measure() -----------------------------------------------------------------------------------

def test_measure_returns_ordered_millisecond_percentiles():
    label, model, contract = _cases()[0]
    stats = bench.measure(model, contract, "duckdb", 200)
    assert set(stats) == {"p50", "p95", "max"}
    assert all(isinstance(v, float) for v in stats.values()), stats
    assert 0.0 <= stats["p50"] <= stats["p95"] <= stats["max"], stats
    # Milliseconds, not seconds or nanoseconds: one compile is well under a second and not 0 ns.
    assert stats["max"] < 1000.0, stats


def test_measure_calls_the_compile_function_exactly_n_times_with_the_inputs():
    _, model, contract = _cases()[0]
    calls = []

    def counting(m, c, d):
        calls.append((m, c, d))
        return "SELECT 1"

    stats = bench.measure(model, contract, "postgres", 37, compile_fn=counting)
    assert len(calls) == 37
    assert all(m is model and c is contract and d == "postgres" for m, c, d in calls)
    assert 0.0 <= stats["p50"] <= stats["p95"] <= stats["max"]


def test_measure_with_n_of_one_has_equal_percentiles():
    _, model, contract = _cases()[0]
    stats = bench.measure(model, contract, "duckdb", 1)
    assert stats["p50"] == stats["p95"] == stats["max"]


@pytest.mark.parametrize("n", [0, -5])
def test_measure_rejects_non_positive_n(n):
    _, model, contract = _cases()[0]
    with pytest.raises(ValueError):
        bench.measure(model, contract, "duckdb", n)


def test_measure_propagates_compile_errors():
    from walt_compiler import CompilerError
    _, model, _ = _cases()[0]
    with pytest.raises(CompilerError):
        bench.measure(model, {"metrics": [{"name": "no_such_metric"}]}, "duckdb", 5)


# --- machine() -----------------------------------------------------------------------------------

def test_machine_has_the_expected_non_empty_string_fields():
    info = bench.machine()
    assert set(info) == MACHINE_KEYS
    for key, value in info.items():
        assert isinstance(value, str) and value.strip(), f"{key}: {value!r}"
    assert info["python"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}")
    assert int(info["cpus"]) >= 1


def test_bench_module_imports_only_stdlib_and_walt_compiler():
    tree = ast.parse((ROOT / "bench" / "bench.py").read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add(node.module.split(".")[0])
    foreign = {r for r in roots if r not in sys.stdlib_module_names and r != "walt_compiler"}
    assert not foreign, f"bench.py imports non-stdlib modules: {sorted(foreign)}"


# --- main() --------------------------------------------------------------------------------------

def test_default_n_is_ten_thousand():
    assert bench.DEFAULT_N == 10_000


def _split(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def test_main_prints_one_row_per_case_and_dialect_and_the_machine_spec(capsys):
    assert bench.main(["--n", "50"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()

    headers = [i for i, line in enumerate(lines) if "|" in line and _split(line)[:2] == ["case", "dialect"]]
    assert len(headers) == 1, out
    assert _split(lines[headers[0]]) == ["case", "dialect", "p50_ms", "p95_ms", "max_ms"]

    rows = [_split(line) for line in lines
            if "|" in line and len(_split(line)) == 5 and _split(line)[0] in EXPECTED_LABELS]
    assert [(r[0], r[1]) for r in rows] == [(lbl, d) for lbl in EXPECTED_LABELS for d in DIALECTS], out
    for r in rows:
        p50, p95, mx = (float(x) for x in r[2:])
        assert 0.0 <= p50 <= p95 <= mx, r

    assert "n=50" in out
    for key, value in bench.machine().items():
        assert f"{key}: {value}" in out, f"machine field {key!r} missing from output"


def test_main_output_layout_is_deterministic_apart_from_the_numbers(capsys):
    def skeleton():
        assert bench.main(["--n", "5"]) == 0
        out = capsys.readouterr().out
        # Blank out the three timing cells of every table row; everything else must match.
        return [_split(line)[:2] + ["#"] * 3 if "|" in line and _split(line)[0] in EXPECTED_LABELS
                else line for line in out.splitlines()]

    assert skeleton() == skeleton()


def test_module_runs_as_a_script():
    proc = subprocess.run([sys.executable, "-m", "bench.bench", "--n", "5"], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "p95_ms" in proc.stdout
    assert proc.stderr == ""


def test_makefile_has_a_bench_target():
    makefile = (ROOT / "Makefile").read_text()
    assert any(line.startswith("bench:") for line in makefile.splitlines())
