"""S12: the command-line interface, `python -m walt_compiler` (DESIGN.md §4 "CLI", §9).

Every test drives the CLI as a real subprocess from the repo root, with relative fixture paths,
exactly as a user would type it. One in-process test at the end gives a readable traceback.

Conventions pinned here (DESIGN.md leaves them open, decided for S12):

  Exit codes     0 success; 1 any error about the inputs (CompilerError, unreadable file,
                 invalid JSON, a seed that fails to load); 2 command-line usage (argparse).
  stdout         `compile`: the SQL followed by exactly one "\\n" (byte-identical to the golden file).
                 `run`: the table. On any error stdout is empty.
  stderr         Empty on success. A CompilerError prints
                     error: <Code> at <path>: <message>
                     did you mean: <s1>, <s2>          (only when there are suggestions)
                 and " at <path>" is omitted when the path is empty. Any other error is exactly
                 one line starting "error: ". Never a Python traceback.
  --dialect      Required for `compile`, passed straight to the compiler (no argparse `choices`,
                 so an unknown name is an UnknownDialect with suggestions). `run` has no --dialect.
  run --seed     Optional; default fixtures/pdf/duckdb_schema.sql, resolved against the repo, not
                 the working directory.
  Table          header line, a separator line, one line per row, each ending in "\\n".
                 Columns are joined by " | " and left-aligned, padded to the widest cell of the
                 column (header included); trailing spaces are stripped from every line.
                 The separator is "-" * width per column, joined by "-+-".
                 Zero rows print the header and separator only. No row-count footer.
  Cells          NULL -> NULL; bool -> true / false; int -> str; float -> repr (420.0);
                 Decimal -> str (1499.50); date -> ISO (2026-01-07);
                 timestamp -> "YYYY-MM-DD HH:MM:SS" (str of the datetime).
"""
import json
import os
import subprocess
import sys

import pytest

from walt_compiler import compile_contract
from tests.helpers import PDF, ROOT, load_json, pdf_contract, pdf_model

PDF_MODEL = "fixtures/pdf/semantic_model.json"
SYN_MODEL = "fixtures/synthetic/semantic_model.json"
SYN_SEED = "fixtures/synthetic/seed.sql"


def cli(*args, cwd=ROOT):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-m", "walt_compiler", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


def write_json(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(json.dumps(doc))
    return str(path)


def assert_failed(proc, exit_code):
    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.returncode == exit_code, (proc.returncode, proc.stderr)
    assert proc.stdout == ""


def assert_one_error_line(proc, *fragments):
    """A non-CompilerError failure: exactly one stderr line, starting 'error: '."""
    assert_failed(proc, 1)
    assert proc.stderr.endswith("\n") and proc.stderr.count("\n") == 1, repr(proc.stderr)
    assert proc.stderr.startswith("error: "), proc.stderr
    for fragment in fragments:
        assert fragment in proc.stderr, (fragment, proc.stderr)


# --- compile: happy path ------------------------------------------------------------------------

@pytest.mark.parametrize("dialect", ["duckdb", "postgres"])
@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_compile_prints_exactly_the_golden_sql(dialect, name):
    proc = cli("compile", "--model", PDF_MODEL, "--contract", f"fixtures/pdf/contracts/{name}.json",
               "--dialect", dialect)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    # The golden file is the SQL plus exactly one trailing newline.
    assert proc.stdout == (PDF / "golden" / dialect / f"{name}.sql").read_text()


def test_compile_output_is_the_library_output_plus_one_newline():
    proc = cli("compile", "--model", SYN_MODEL,
               "--contract", "fixtures/synthetic/contracts/riders_by_electric_compare.json",
               "--dialect", "postgres")
    assert proc.returncode == 0, proc.stderr
    expected = compile_contract(load_json(ROOT / SYN_MODEL),
                                load_json(ROOT / "fixtures/synthetic/contracts/riders_by_electric_compare.json"),
                                "postgres")
    assert proc.stdout == expected + "\n"


# --- CompilerError reporting --------------------------------------------------------------------

def _library_error(model, contract, dialect):
    with pytest.raises(Exception) as exc:
        compile_contract(model, contract, dialect)
    return exc.value


def test_unknown_metric_prints_code_path_message_and_suggestion(tmp_path):
    contract = {"metrics": [{"name": "total_revenu"}], "group_by": []}
    proc = cli("compile", "--model", PDF_MODEL, "--contract", write_json(tmp_path, "c.json", contract),
               "--dialect", "duckdb")
    assert_failed(proc, 1)
    err = _library_error(pdf_model(), contract, "duckdb")
    assert (err.code, err.path) == ("UnknownMetric", "/metrics/0/name")
    # 'total_revenu' -> total_revenue: distance 1 / 13; order_count is far off.
    assert proc.stderr == (f"error: UnknownMetric at /metrics/0/name: {err.message}\n"
                           "did you mean: total_revenue\n")


def test_several_suggestions_are_comma_separated_in_order(tmp_path):
    # 'aud_count' vs audit_count: 2 / 11 = 0.18; vs trip_count: 4 / 10 = 0.4 (<= 0.4, included).
    contract = {"metrics": [{"name": "aud_count"}], "group_by": []}
    proc = cli("compile", "--model", SYN_MODEL, "--contract", write_json(tmp_path, "c.json", contract),
               "--dialect", "duckdb")
    assert_failed(proc, 1)
    lines = proc.stderr.splitlines()
    assert lines[0].startswith("error: UnknownMetric at /metrics/0/name: ")
    assert lines[1:] == ["did you mean: audit_count, trip_count"]


def test_unknown_dialect_has_no_path_and_suggests_the_close_name():
    proc = cli("compile", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json",
               "--dialect", "duckbd")
    assert_failed(proc, 1)
    err = _library_error(pdf_model(), pdf_contract("a"), "duckbd")
    assert (err.code, err.path) == ("UnknownDialect", "")
    # Empty path: the " at <path>" part is omitted. 'duckbd' -> duckdb: distance 2 / 6.
    assert proc.stderr == f"error: UnknownDialect: {err.message}\ndid you mean: duckdb\n"


def test_error_without_suggestions_prints_a_single_line(tmp_path):
    contract = {"metrics": [{"name": "total_revenue"}], "group_by": [],
                "filters": [{"field": "fiscal_year", "op": "=", "value": "twenty"}]}
    proc = cli("compile", "--model", PDF_MODEL, "--contract", write_json(tmp_path, "c.json", contract),
               "--dialect", "duckdb")
    assert_failed(proc, 1)
    err = _library_error(pdf_model(), contract, "duckdb")
    assert (err.code, err.path, err.suggestions) == ("InvalidLiteral", "/filters/0/value", ())
    assert proc.stderr == f"error: InvalidLiteral at /filters/0/value: {err.message}\n"


def test_run_reports_compiler_errors_the_same_way(tmp_path):
    contract = {"metrics": [{"name": "total_revenu"}], "group_by": []}
    proc = cli("run", "--model", PDF_MODEL, "--contract", write_json(tmp_path, "c.json", contract))
    assert_failed(proc, 1)
    assert proc.stderr.splitlines()[0].startswith("error: UnknownMetric at /metrics/0/name: ")
    assert proc.stderr.splitlines()[1:] == ["did you mean: total_revenue"]


# --- input-file errors: one clean line, exit 1 --------------------------------------------------

def test_missing_contract_file():
    proc = cli("compile", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/nope.json",
               "--dialect", "duckdb")
    assert_one_error_line(proc, "fixtures/pdf/contracts/nope.json")


def test_missing_model_file():
    proc = cli("run", "--model", "fixtures/nope_model.json", "--contract", "fixtures/pdf/contracts/a.json")
    assert_one_error_line(proc, "fixtures/nope_model.json")


def test_invalid_json_contract(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"metrics": [')
    proc = cli("compile", "--model", PDF_MODEL, "--contract", str(path), "--dialect", "duckdb")
    assert_one_error_line(proc, str(path), "JSON")


def test_missing_seed_file():
    proc = cli("run", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json",
               "--seed", "fixtures/nope_seed.sql")
    assert_one_error_line(proc, "fixtures/nope_seed.sql")


def test_seed_that_fails_to_load(tmp_path):
    seed = tmp_path / "bad_seed.sql"
    seed.write_text("CREATE TABLE oops (;\n")
    proc = cli("run", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json", "--seed", str(seed))
    assert_one_error_line(proc, str(seed))


def test_query_that_fails_on_a_seed_missing_a_column(tmp_path):
    # The seed loads fine, but fact_sales.revenue is renamed to amount (the INSERTs are positional,
    # so they still load), and Contract A's SUM("fact_sales"."revenue") fails at execution time.
    ddl = (PDF / "duckdb_schema.sql").read_text()
    assert "  revenue DOUBLE\n" in ddl
    seed = tmp_path / "renamed_revenue.sql"
    seed.write_text(ddl.replace("  revenue DOUBLE\n", "  amount DOUBLE\n"))
    proc = cli("run", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json", "--seed", str(seed))
    assert_one_error_line(proc, str(seed))
    assert "query" in proc.stderr.lower(), proc.stderr  # says the query failed; DuckDB's wording not pinned


# --- usage errors: argparse, exit 2 -------------------------------------------------------------

def test_compile_requires_dialect():
    proc = cli("compile", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json")
    assert_failed(proc, 2)
    assert "--dialect" in proc.stderr


def test_compile_requires_contract():
    proc = cli("compile", "--model", PDF_MODEL, "--dialect", "duckdb")
    assert_failed(proc, 2)
    assert "--contract" in proc.stderr


def test_run_does_not_accept_dialect():
    proc = cli("run", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json", "--dialect", "duckdb")
    assert_failed(proc, 2)
    assert "--dialect" in proc.stderr


@pytest.mark.parametrize("args", [(), ("explain",)], ids=["no-command", "unknown-command"])
def test_missing_or_unknown_subcommand(args):
    proc = cli(*args)
    assert_failed(proc, 2)
    assert proc.stderr != ""


# --- run: tables --------------------------------------------------------------------------------

def assert_table_output(proc, lines):
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == "".join(line + "\n" for line in lines), "\n" + proc.stdout


# fixtures/pdf/expected/a.json: online_rev 420.0, total_revenue 710.0 (FY2026: 120+80+300+50+70+90 = 710,
# Online 120 + 300 = 420). Widths 10 and 13.
CONTRACT_A_TABLE = [
    "online_rev | total_revenue",
    "-----------+--------------",
    "420.0      | 710.0",
]


def test_run_contract_a_with_the_default_pdf_seed():
    proc = cli("run", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json")
    assert_table_output(proc, CONTRACT_A_TABLE)


def test_run_contract_a_with_the_pdf_seed_given_explicitly():
    proc = cli("run", "--model", PDF_MODEL, "--contract", "fixtures/pdf/contracts/a.json",
               "--seed", "fixtures/pdf/duckdb_schema.sql")
    assert_table_output(proc, CONTRACT_A_TABLE)


def test_run_default_seed_does_not_depend_on_the_working_directory(tmp_path):
    proc = cli("run", "--model", str(ROOT / PDF_MODEL), "--contract", str(PDF / "contracts" / "a.json"),
               cwd=tmp_path)
    assert_table_output(proc, CONTRACT_A_TABLE)


def test_run_zero_rows_prints_header_and_separator_only(tmp_path):
    contract = {"metrics": [{"name": "total_revenue"}], "group_by": ["region"],
                "filters": [{"field": "fiscal_year", "op": "=", "value": 1999}]}
    proc = cli("run", "--model", PDF_MODEL, "--contract", write_json(tmp_path, "c.json", contract))
    assert_table_output(proc, [
        "region | total_revenue",
        "-------+--------------",
    ])


def test_run_synthetic_contract_with_seed():
    # Hand-computed in tests/synthetic_helpers.py (CONTRACT_EXPECTED["fare_variants_by_day"]):
    #   05: 12.0  9.0  8.0  3.0      06: 8.0  6.0  2.0  NULL      07: 10.0  3.0  7.0  NULL
    # Widths: trip_date 10 (the dates), fare_total 10, electric_fare 13, harbor_fare 11, nl_member_fare 14.
    proc = cli("run", "--model", SYN_MODEL, "--contract", "fixtures/synthetic/contracts/fare_variants_by_day.json",
               "--seed", SYN_SEED)
    assert_table_output(proc, [
        "trip_date  | fare_total | electric_fare | harbor_fare | nl_member_fare",
        "-----------+------------+---------------+-------------+---------------",
        "2026-01-05 | 12.0       | 9.0           | 8.0         | 3.0",
        "2026-01-06 | 8.0        | 6.0           | 2.0         | NULL",
        "2026-01-07 | 10.0       | 3.0           | 7.0         | NULL",
    ])


def test_run_prints_every_cell_type(tmp_path):
    # trip_date 2026-01-07 keeps T07 and T08 (tests/synthetic_helpers.py):
    #   T07 started 10:00:00, cycle C9 is an orphan -> list_price NULL, electric NULL; fare 7.0
    #   T08 started 11:11:11, C3 -> CM-C list_price 1499.50 (DECIMAL(8,2)), electric true; fare 3.0
    #   total: fare 7 + 3 = 10.0, trips 2
    # Widths: start_time 19 (timestamps), list_price 10, electric 8, fare_total 10, trip_count 10, is_total 8.
    contract = {"metrics": [{"name": "fare_total"}, {"name": "trip_count"}],
                "group_by": ["start_time", "list_price", "electric"],
                "filters": [{"field": "trip_date", "op": "=", "value": "2026-01-07"}],
                "totals": "grand"}
    proc = cli("run", "--model", SYN_MODEL, "--contract", write_json(tmp_path, "c.json", contract),
               "--seed", SYN_SEED)
    assert_table_output(proc, [
        "start_time          | list_price | electric | fare_total | trip_count | is_total",
        "--------------------+------------+----------+------------+------------+---------",
        "2026-01-07 10:00:00 | NULL       | NULL     | 7.0        | 1          | false",
        "2026-01-07 11:11:11 | 1499.50    | true     | 3.0        | 1          | false",
        "NULL                | NULL       | NULL     | 10.0       | 2          | true",
    ])


# --- in-process variant (readable traceback when the entry point itself breaks) -----------------

def test_main_in_process_compile(capsys):
    from walt_compiler.__main__ import main

    code = main(["compile", "--model", str(ROOT / PDF_MODEL), "--contract", str(PDF / "contracts" / "a.json"),
                 "--dialect", "duckdb"])
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert out == (PDF / "golden" / "duckdb" / "a.sql").read_text()
