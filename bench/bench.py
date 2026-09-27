"""Compile-time benchmark: p50/p95/max over n compiles per contract x dialect (DESIGN.md §10).

Percentiles use the nearest-rank method on the n individually timed calls; there is no warm-up.
"""
import argparse
import json
import math
import os
import platform
import sys
import time
from pathlib import Path

from walt_compiler import compile_contract

DEFAULT_N = 10_000
DIALECTS = ("duckdb", "postgres")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def cases():
    """Yield (label, model, contract): the PDF contracts A, B, C, then the synthetic ones by file name."""
    pdf_model = _load(FIXTURES / "pdf" / "semantic_model.json")
    for name in ("a", "b", "c"):
        yield name.upper(), pdf_model, _load(FIXTURES / "pdf" / "contracts" / f"{name}.json")
    synth_model = _load(FIXTURES / "synthetic" / "semantic_model.json")
    for path in sorted((FIXTURES / "synthetic" / "contracts").glob("*.json"), key=lambda p: p.name):
        yield f"synthetic/{path.stem}", synth_model, _load(path)


def _nearest_rank(ordered: list[float], pct: float) -> float:
    return ordered[max(1, math.ceil(pct / 100 * len(ordered))) - 1]


def measure(model: dict, contract: dict, dialect: str, n: int, *, compile_fn=compile_contract) -> dict:
    """Time n calls of compile_fn(model, contract, dialect); return p50, p95 and max in ms."""
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        compile_fn(model, contract, dialect)
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    return {"p50": _nearest_rank(samples, 50), "p95": _nearest_rank(samples, 95), "max": samples[-1]}


def machine() -> dict:
    """Python and host details that the numbers depend on."""
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine() or "unknown",
        # platform.processor() is "" on most Linux builds.
        "processor": platform.processor() or platform.machine() or "unknown",
        "cpus": str(os.cpu_count() or 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"compiles per case x dialect (default {DEFAULT_N})")
    args = parser.parse_args(argv)

    rows = [("case", "dialect", "p50_ms", "p95_ms", "max_ms")]
    for label, model, contract in cases():
        for dialect in DIALECTS:
            stats = measure(model, contract, dialect, args.n)
            rows.append((label, dialect, *(f"{stats[k]:.3f}" for k in ("p50", "p95", "max"))))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print(" | ".join(cell.ljust(w) if i < 2 else cell.rjust(w)
                         for i, (cell, w) in enumerate(zip(row, widths))))
    print()
    print(f"n={args.n}")
    for key, value in machine().items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
