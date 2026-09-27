"""Command-line interface: `python -m walt_compiler compile|run` (DESIGN.md §4 "CLI").

`compile` prints the SQL. `run` compiles for DuckDB, loads a seed into an in-memory DuckDB and
prints the result as a table. Exit codes: 0 ok, 1 any input problem, 2 usage (argparse).
Every failure is reported on stderr with stdout left empty; never a traceback.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

from walt_compiler import compile_contract
from walt_compiler.errors import CompilerError

# Resolved against the package, so `run` works from any working directory.
DEFAULT_SEED = Path(__file__).resolve().parent.parent / "fixtures" / "pdf" / "duckdb_schema.sql"


class InputError(Exception):
    """A problem with an input file that is not a CompilerError: reported as one stderr line."""


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = _compile(args) if args.command == "compile" else _run(args)
    except CompilerError as err:
        where = f" at {err.path}" if err.path else ""
        print(f"error: {err.code}{where}: {err.message}", file=sys.stderr)
        if err.suggestions:
            print(f"did you mean: {', '.join(err.suggestions)}", file=sys.stderr)
        return 1
    except InputError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m walt_compiler",
                                     description="Compile a query contract into SQL.")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_cmd = commands.add_parser("compile", help="print the SQL for a contract")
    run_cmd = commands.add_parser("run", help="compile for DuckDB, execute on a seed and print the table")
    for cmd in (compile_cmd, run_cmd):
        cmd.add_argument("--model", required=True, help="semantic model JSON")
        cmd.add_argument("--contract", required=True, help="query contract JSON")
    # No `choices`: an unknown name reaches the compiler and becomes an UnknownDialect with suggestions.
    compile_cmd.add_argument("--dialect", required=True, help="target dialect, e.g. duckdb or postgres")
    run_cmd.add_argument("--seed", help=f"SQL to load before running (default: {DEFAULT_SEED})")
    return parser


def _compile(args: argparse.Namespace) -> str:
    return compile_contract(_load_json(args.model, "model"), _load_json(args.contract, "contract"),
                            args.dialect) + "\n"


def _run(args: argparse.Namespace) -> str:
    sql = compile_contract(_load_json(args.model, "model"), _load_json(args.contract, "contract"), "duckdb")
    seed_path = args.seed or str(DEFAULT_SEED)
    seed = _read(seed_path, "seed")
    import duckdb  # only `run` needs it; the compiler itself is stdlib-only

    con = duckdb.connect(":memory:")
    try:
        con.execute(seed)
    except duckdb.Error as err:
        raise InputError(f"seed {seed_path} failed to load: {_one_line(err)}") from None
    try:
        cursor = con.execute(sql)
        header = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
    except duckdb.Error as err:
        raise InputError(f"query failed on seed {seed_path}: {_one_line(err)}") from None
    return format_table(header, rows)


def _read(path: str, what: str) -> str:
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError) as err:
        reason = err.strerror if isinstance(err, OSError) and err.strerror else str(err)
        raise InputError(f"cannot read {what} {path}: {reason}") from None


def _load_json(path: str, what: str) -> object:
    text = _read(path, what)
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise InputError(f"{what} {path} is not valid JSON: {err.msg} "
                         f"(line {err.lineno}, column {err.colno})") from None


def _one_line(err: Exception) -> str:
    return " ".join(str(err).split())


def format_table(header: list[str], rows: list[tuple]) -> str:
    """Left-aligned columns joined by ' | ', a '-+-' separator, trailing spaces stripped."""
    cells = [list(header)] + [[_cell(value) for value in row] for row in rows]
    widths = [max(len(line[i]) for line in cells) for i in range(len(header))]
    lines = [" | ".join(cell.ljust(width) for cell, width in zip(line, widths)) for line in cells]
    lines.insert(1, "-+-".join("-" * width for width in widths))
    return "".join(line.rstrip() + "\n" for line in lines)


def _cell(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime.datetime):
        return str(value)
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)  # int, Decimal, text


if __name__ == "__main__":
    sys.exit(main())
