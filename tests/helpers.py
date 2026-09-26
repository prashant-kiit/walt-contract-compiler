"""Shared test helpers: fixture loading and result comparison."""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
PDF = FIXTURES / "pdf"


def load_json(path: Path):
    return json.loads(path.read_text())


def pdf_model() -> dict:
    return load_json(PDF / "semantic_model.json")


def pdf_contract(name: str) -> dict:
    return load_json(PDF / "contracts" / f"{name}.json")


def pdf_expected(name: str) -> dict:
    return load_json(PDF / "expected" / f"{name}.json")


def _cell_equal(actual, expected) -> bool:
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        return actual == expected and type(actual) is type(expected)
    if actual is None or isinstance(actual, bool):
        return False
    return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)


def assert_result(columns, rows, expected: dict) -> None:
    """Compare a query result with an expected {columns, rows} fixture.

    Row order matters (the compiler emits an explicit ORDER BY). Numbers are compared
    with a tight tolerance; everything else exactly.
    """
    assert list(columns) == expected["columns"]
    assert len(rows) == len(expected["rows"]), f"row count {len(rows)} != {len(expected['rows'])}: {rows}"
    for i, (got, want) in enumerate(zip(rows, expected["rows"])):
        assert len(got) == len(want), f"row {i}: {got} vs {want}"
        for j, (a, e) in enumerate(zip(got, want)):
            assert _cell_equal(a, e), f"row {i} col {expected['columns'][j]!r}: got {a!r}, want {e!r}"
