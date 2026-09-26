# Implementation Plan: Walt Contract → SQL Compiler (TDD, HITL, commit & push per slice)

## Context

This is the Walt.ai Level-2 take-home: compile a JSON query contract plus a semantic model into SQL for DuckDB and Postgres.
- Under 10 ms, byte-identical output, honest named errors, tests, and a README.
- There is 1 day left.
- The design is locked in `DESIGN.md` (repo root). This plan covers only *how* we build it: test-first, with the user reviewing every slice, and every approved slice committed and pushed to `origin/master`.

The repo is currently empty apart from `.gitignore`. `DESIGN.md` and the PDF are untracked. Remote: `origin` → github.com/prashant-kiit/walt-contract-compiler. Tools: Python 3.13.5, Docker 28.2.2.

No new scope. The last two open questions (a dialect allow-list, and `run --dialect postgres`) were never agreed, so they are **excluded**.

## Working agreement

**Every slice follows this loop:**

1. **RED:** write the tests for the slice from DESIGN.md, run them, and show them failing.
2. **🛑 Gate 1: the user reviews the tests.** Do the tests say the right thing? No code is written before approval.
3. **GREEN:** write the minimum code that passes. Run the full suite, not just the new tests.
4. **REFACTOR** only while tests stay green.
5. **🛑 Gate 2: the user reviews the code, the diff and the green test output.**
6. On approval, make **one commit (tests and code together, so every commit is green)** and `git push origin master`.

**Rules:**
- Commit messages use Conventional Commits (`feat(resolve): …`, `test(…)`, `chore(…)`, `docs(…)`) and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Nothing is committed or pushed without Gate 2 approval.
- If a slice shows DESIGN.md is wrong or ambiguous, stop, discuss, update DESIGN.md (in the same commit), then continue.
- The PDF is never committed. It goes in `.gitignore`.
- Expected values in tests are computed by hand from the fixture data and never adjusted to match the code. A change to an expected value needs explicit approval (DESIGN.md §13).

## Slices

Time estimates are rough, about 1 day in total. Each slice lists **tests first**, then the code that makes them pass.

### S0. Scaffolding (~30 min)
- **Tests:** `tests/test_smoke.py` (the package imports; the DuckDB fixture loads and `SELECT count(*) FROM fact_sales` = 9).
- **Code:**
  - `pyproject.toml` (dev deps: `duckdb`, `pytest`, `psycopg[binary]`), `Makefile` (`setup`, `test`, `test-pg`, `bench`, `run`), a `.venv`
  - `.gitignore` (+ the PDF, `.venv`, caches), and the `walt_compiler/` package skeleton
- **Fixtures:**
  - `fixtures/pdf/semantic_model.json` (the PDF model + dimension `type`s)
  - `duckdb_schema.sql` and `postgres_schema.sql` (the PDF data; Postgres uses `DOUBLE PRECISION`)
  - `contracts/{a,b,c}.json`, `expected/{a,b,c}.json` (hand-computed)
  - `fixtures/orphan_rows.sql`
- **Docs:** commit `DESIGN.md` and a copy of this plan as `PLAN.md`.
- **Commit:** `chore: scaffold project, fixtures and design docs`

### S1. Errors + suggestions (~30 min)
- **Tests:**
  - Levenshtein distances
  - normalised score, threshold 0.4 and max 3 suggestions
  - sort order (score, name), case-insensitive matching
  - `CompilerError` carries code, path, message and suggestions
  - the formatted message is deterministic
- **Code:** `errors.py` (full hierarchy from DESIGN.md §9), `suggest.py`, `CompileOptions`.

### S2. Registries (~45 min)
- **Tests:**
  - type coercion for each of the 7 types: good and bad literals, `"2025"` → 2025 for integer, ISO strings to date/timestamp, bool strictness (`1` is not a bool)
  - operator arity and allowed types (no `<` on boolean; `in` needs a non-empty list)
  - aggregation entries: func, distinct, ignores-NULLs flag, and `measure_class` consistency
- **Code:** `types.py`, `operators.py`, `aggregations.py`.

### S3. Shape rules + Catalog (~1 h)
- **Tests:**
  - the model's shape: missing/unknown keys, wrong kinds, bad enums, with JSON-pointer paths and suggestions for unknown keys
  - cross-references: metric/dimension/relationship pointing at an undeclared dataset, duplicate names per namespace, missing `type`, `column` defaulting to `name`
  - the PDF model builds a Catalog whose contents we assert
- **Code:** `schema.py` (declarative rules and a small rule interpreter), `model.py` (Catalog).

### S4. Contract parsing (~45 min)
- **Tests:**
  - A, B and C parse into the expected typed trees
  - the contract's shape: unknown keys, `metrics` empty, duplicate group_by, `totals` enum, compare shape, filter shape, operator arity, all with paths
- **Code:** `contract.py`, reusing `schema.py`.

### S5. Join planning (~45 min)
- **Tests**, on small in-test models:
  - direct join; multi-hop snowflake join
  - only the datasets that are needed get joined
  - order is deterministic (declaration order)
  - `AmbiguousJoinPath`, `NoJoinPath`
  - `one_to_many`/`many_to_many` → `UnsupportedRelationship`; `one_to_one` is followed; no reverse traversal
- **Code:** `joins.py`.

### S6. Resolve (base features) → LogicalPlan (~1 h)
- **Tests:** plan-level assertions (no SQL):
  - Contract A's plan equals the one hand-written in the chat and DESIGN.md
  - `UnknownMetric`/`UnknownDimension` with suggestions, `ModelMismatch`, `InvalidLiteral`, `MultipleFactTables`, `DuplicateOutputName`
  - per-metric filters become conditional measures
- **Code:** `plan.py` (IR + PlanBuilder), `resolve.py`, `features/metric_filters.py`.

### S7. SQL AST + lowering + DuckDB renderer: first end-to-end (~1 h)
- **Tests:**
  - identifier quoting and literal rendering per type, with escaping (`O'Brien`)
  - **acceptance: Contract A executes on DuckDB and gives `online_rev=420, total_revenue=710`**
  - golden SQL snapshot for A
  - determinism: 100 compiles give identical output
- **Code:** `sqlast.py`, `lower.py`, `dialects/{__init__,base,duckdb}.py`, and `compile_contract()` in `__init__.py`.

### S8. Compare (~1 h)
- **Tests:**
  - plan and SQL for B without totals
  - values, delta and pct on DuckDB
  - the `InvalidCompare` cases (≠2 periods, primary not in periods, dimension in group_by, bad outputs) and `ConflictingFilter`
  - period names used verbatim in column names
  - compare + per-metric filter gives AND conditions
- **Code:** `features/compare.py`.

### S9. Totals (~45 min)
- **Tests:**
  - **acceptance: B's full expected table incl. Total 450/710/260/57.8**
  - **acceptance: C gives 4/3/2 + total 8**
  - orphan rows: NULL region row vs `is_total`, and a filter excludes the orphans
  - `totals` with empty group_by → error; `ORDER BY` with the total last and `NULLS LAST`
  - golden SQL for B and C
  - cross-process determinism with different `PYTHONHASHSEED`
- **Code:** `features/totals.py`, plus the ordering/output finalisation in `resolve.py`.

### S10. Beyond-PDF tests by an independent agent (~1.5 h)
- Spawn a **separate agent** (as the user requested) whose only inputs are `DESIGN.md` and the PDF. It never sees `walt_compiler/`.
  - It writes `fixtures/synthetic/` (a model sharing no names with the PDF's, multi-hop, all types and aggregations, `column ≠ name`) with hand-computed expectations.
  - It also writes the §6.7 combination tests and edge cases.
- **🛑 Gate 1:** the user reviews these tests.
- Then comes the fix loop: **fix the code, never the expectations**. Any disputed expectation comes back to the user.
- **🛑 Gate 2**, then commit and push.

### S11. Postgres dialect (~1 h)
- **Tests:**
  - `docker-compose.yml` (postgres:16) and a pytest `postgres` marker
  - A/B/C (+ the synthetic model) run on Postgres and match the expected results
  - golden SQL for Postgres
  - dialect-specific division by zero: Postgres raises an error, and DuckDB's actual native result is pinned (verified at this point, not assumed)
  - `UnknownDialect` with suggestions
  - the CASE-WHEN fallback, tested through a test-only dialect with `supports_aggregate_filter=False`
- **Code:** `dialects/postgres.py`.

### S12. CLI (~30 min)
- **Tests:**
  - `compile` prints SQL and exits 0
  - an invalid contract prints code/path/suggestions to stderr and exits ≠0
  - `run` on A prints the expected table
  - `run --seed` with a synthetic seed
- **Code:** `__main__.py` (argparse, table printer).

### S13. Benchmark (~20 min)
- **Test:** p95 < 10 ms per contract.
- **Code:** `bench/bench.py` measures p50/p95/max over 10k compiles per contract (A/B/C + synthetic), for both dialects, and records the machine specs.

### S14. README (~2 h, protected time)
- **Contents:**
  - shape and reasoning
  - join types and why
  - totals, compare and NULL semantics
  - division-by-zero stance
  - how to add `having` and a monthly trend
  - what breaks first at 10× vocabulary
  - measured timings
  - what was cut and what comes next
  - how to run
- **Gate:** the user reviews the prose (Gate 2 only).

## Cut order if time runs short

Cut in this order, and write each cut up in the README:
1. `timestamp`/`decimal` types
2. `avg`/`min`/`max`
3. the CLI `run --seed`
4. cross-process determinism

**Never cut:** A/B/C acceptance tests, totals correctness, the join-semantics tests, the synthetic-model generality tests, and README time.

## Critical files

- `walt_compiler/{errors,suggest,schema,types,operators,aggregations,model,contract,joins,plan,resolve,sqlast,lower}.py`
- `walt_compiler/features/*`, `walt_compiler/dialects/*`, `walt_compiler/__main__.py`
- `fixtures/pdf/*`, `fixtures/synthetic/*`, `tests/*`, `bench/bench.py`
- `Makefile`, `pyproject.toml`, `docker-compose.yml`, `README.md`, `DESIGN.md`, `PLAN.md`

## Verification (end to end)

- `make setup && make test`: every DuckDB-side test passes, including A/B/C acceptance, golden SQL and determinism.
- `make test-pg`: starts Postgres in Docker and runs the Postgres suite, which passes.
- `make bench`: p95 < 10 ms per contract; the numbers are copied into the README.
- `python -m walt_compiler run --model fixtures/pdf/semantic_model.json --contract fixtures/pdf/contracts/b.json` prints the PDF's table.
- `git log` shows one green, reviewed commit per slice; `origin/master` is up to date.
