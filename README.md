# Query contract → SQL compiler

`compile_contract(semantic_model, contract, dialect) -> str` turns a JSON query contract into SQL for DuckDB or
Postgres. It is deterministic (byte-identical output), makes no I/O, uses only the Python standard library, and compiles in
about 0.1 ms at p95 (see [Performance](#6-measured-performance)).

## 0. Quick start

Requires Python ≥ 3.11. Docker is only needed for the Postgres tests.

```sh
make setup      # venv + dev dependencies (duckdb, psycopg, pytest)
make run        # Contract A, compiled for DuckDB, run on the PDF data
make test       # the whole suite on DuckDB (629 tests)
make test-pg    # starts Postgres 16 in Docker and runs the same contracts there (75 tests)
make bench      # p50 / p95 / max over 10,000 compiles per contract and dialect
```

```sh
python -m walt_compiler compile --model fixtures/pdf/semantic_model.json \
    --contract fixtures/pdf/contracts/b.json --dialect postgres
python -m walt_compiler run --model M.json --contract C.json [--seed schema.sql]
```

A bad contract exits with status 1 and a precise error, never a traceback:

```
error: UnknownMetric at /metrics/0/name: 'total_revenu' is not a metric.
did you mean: total_revenue
```

`DESIGN.md` is the full specification. This README is the reasoning.

## 1. The shape: a small compiler, not a template filler

I treated this as a compiler problem. The contract is a program in a tiny language, the semantic model is its
symbol table, and SQL is the target. Each stage knows exactly one thing:

```
semantic model ─▶ shape rules ─▶ Catalog (names, types, join edges)
contract ───────▶ shape rules ─▶ Contract tree (typed)
                                   │
                  resolve + features/*  ─▶ LogicalPlan     knows contracts, not SQL
                                   │
                              lower.py  ─▶ SQL AST         knows SQL structure, not spelling
                                   │
                           dialects/*   ─▶ string          knows spelling, not contracts
```

I chose this shape for three reasons.

- **Errors happen at the earliest stage that can know about them.** A misspelled key fails in the shape rules. An
  unknown metric or an ill-typed literal (`"2025x"` against an INTEGER column) fails in resolve. None of them reaches
  SQL generation. There is no path where a guess becomes SQL.
- **Semantics live in one place.** The questions where analytics goes wrong (which rows a total is computed from,
  which joins a filter needs, what a period means) are all settled in the `LogicalPlan`, before SQL exists. The
  lowering step is mechanical.
- **Nothing is assembled from strings.** SQL is built as a tree and printed by a dialect. Contract values are only
  ever rendered as typed literals by the dialect, so quoting and escaping are correct by construction, and output is
  byte-identical because every step iterates in a declared order.

**Where the dialect line is drawn.** Everything above the renderer is dialect-free. The Postgres dialect is
[7 lines](walt_compiler/dialects/postgres.py): a name and an identifier limit. For this vocabulary, DuckDB and
Postgres spell SQL the same way, so the byte-identical golden files for both are a result, not a shortcut. The
real differences are in runtime behaviour, which I cover in §2. Dialects declare capabilities rather than branching in
the core. `supports_aggregate_filter = False` switches to `CASE WHEN` inside aggregates. That path is tested with a
test-only dialect and gives identical results. There is one place where dialect knowledge crosses the line on
purpose: resolve receives the dialect's maximum identifier length as a plain number, so it can refuse names Postgres
would silently truncate.

**What is data, not code.** Aggregations, filter operators, dimension types, dialects and the JSON shape rules are
registries. Adding an aggregation like `median` or a new operator is one registry entry. Each contract feature (`metric_filters`,
`compare`, `totals`) is its own module in `features/` that adds measures, predicates, grouping sets or output
columns to a shared plan builder. A new feature is a new module, not an edit to the others.

**Why no libraries.** The compiler is about 1,700 lines of standard-library Python, and I use no SQL-builder or parser. A
library like SQLGlot would do the parts I most want to be able to explain (quoting, literal rendering, dialect
spelling), and the one-to-one mapping from the plan to the SQL AST is small. DuckDB, psycopg and pytest are
used only to run and test the SQL.

**One extension to the PDF's model format.** Dimensions declare a `type` (`text`, `integer`, `decimal`, `date`, …).
The compiler never reads the warehouse, so without a type it cannot know that `"2025"` must be rendered as `2025`
against an INTEGER column, or `DATE '2026-01-05'` against a DATE. Every other addition is optional.

## 2. Semantic correctness: where analytics quietly lies

This is the section I spent the most time on. Here is Contract B's SQL in full, because it shows almost every
decision at once:

```sql
SELECT
  "region",
  "total_revenue_2025",
  "total_revenue_2026",
  "total_revenue_2026" - "total_revenue_2025" AS "total_revenue_delta",
  100.0 * ("total_revenue_2026" - "total_revenue_2025") / NULLIF("total_revenue_2025", 0) AS "total_revenue_pct_change",
  "is_total"
FROM (
  SELECT
    "dim_store"."region" AS "region",
    SUM("fact_sales"."revenue") FILTER (WHERE "dim_calendar"."fiscal_year" = 2025) AS "total_revenue_2025",
    SUM("fact_sales"."revenue") FILTER (WHERE "dim_calendar"."fiscal_year" = 2026) AS "total_revenue_2026",
    GROUPING("dim_store"."region") = 1 AS "is_total"
  FROM "fact_sales"
  LEFT JOIN "dim_store" ON "fact_sales"."store_id" = "dim_store"."store_id"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" IN (2025, 2026)
  GROUP BY GROUPING SETS (("dim_store"."region"), ())
) AS "agg"
ORDER BY "is_total" ASC, "region" ASC NULLS LAST
```

**Joins: always `LEFT JOIN` from the fact, and only along many-to-one edges.**
- A sale whose store is missing from `dim_store` is still revenue. It lands in a `NULL` region rather than vanishing.
  `fixtures/orphan_rows.sql` adds exactly such rows (an unknown store, an unknown date), and the tests assert they
  are counted.
- A filter on a dimension goes in `WHERE`, after the join, so `fiscal_year = 2026` genuinely restricts. The orphan
  row with no calendar match is excluded, as it should be.
- Join paths are found by breadth-first search over the declared relationships, multi-hop included. Only the datasets the
  query needs are joined, each exactly once. Two equally short paths raise `AmbiguousJoinPath` instead of picking one.
- `one_to_many` and `many_to_many` edges are refused with `UnsupportedRelationship`. Following one would fan out the
  fact rows and silently inflate every sum. I never emit `INNER`, and I never walk a relationship backwards.

**Per-metric filters are conditional aggregation, not subqueries.** Contract A's `online_rev` is
`SUM(revenue) FILTER (WHERE channel = 'Online')` next to a plain `SUM(revenue)` in the same scan. A contract-level
filter and a per-metric filter combine as AND. The same metric can appear any number of times with different aliases and filters.

**Totals are recomputed from base rows, never assembled from the rows above.** `GROUPING SETS ((region), ())` makes
the database aggregate the total row from the fact rows themselves. That is why Contract C's total is **8 distinct
orders, not 9**: O-203 has lines in two regions and is counted once at the total's own grain. Derived columns are
computed in the outer query from each row's *own* cells, so B's total `pct_change` is 260 / 450 = **57.8%**, not the
195% sum of the column. The total row is marked by `is_total` (from `GROUPING()`), not by a `NULL` region, because a
`NULL` region already means "sales whose store is unknown".

**Compare turns periods into columns.** Each metric becomes one conditional measure per period, and the contract gets
an implied `WHERE fiscal_year IN (2025, 2026)`. A contract-level filter on the compare dimension itself is refused
(`ConflictingFilter`), because `fiscal_year = 2026` plus a 2025-vs-2026 compare would silently empty the 2025 column.

**NULL and zero rules.** I pinned each of these with tests on both engines.
- A group with no rows in a period gives `0` for `count` and `count_distinct` (a count of nothing really is zero)
  and `NULL` for `sum`, `min`, `max` and `avg`. I don't coalesce a sum to 0, because that would erase the difference
  between "no sales" and "sales that netted to zero". Showing 0 is a presentation choice, which the consumer can make.
- `pct_change` from a zero base is `NULL`, via `NULLIF(base, 0)`. A percentage change from zero is undefined. Left to the
  engines, the same contract **failed the whole query** on Postgres (`division_by_zero`) and returned `inf`/`NaN` on
  DuckDB, which is not valid JSON. With the count rule above, a zero base is routine (any new region), so I define it
  once, the same everywhere. The delta still carries the change.
- Filters use standard SQL NULL semantics. A row with an unknown dimension value matches no comparison, `!=`
  included. I don't rewrite `!=` to `IS DISTINCT FROM`, so all operators behave the same way.

## 3. Honest failure

Every refusal is a named `CompilerError` with a `code`, a message and a JSON-pointer `path` into the contract or
model, so the system that filled in the contract can see exactly which field to fix:

```
error: InvalidLiteral at /filters/0/value: '2025x' is not a valid integer literal.
error: UnsupportedFeature at /totals: totals 'subtotals' is not supported (supported: grand).
error: InvalidContract at /having: unknown key 'having'.
```

- There are 18 error types. They cover unknown names, literals of the wrong type, operators the type does not allow
  (no `<` on a boolean), unreachable or ambiguous join paths, fan-out relationships, a second fact table, compare
  conflicts, duplicate output names and identifiers the target dialect would truncate.
- Unknown names come with "did you mean" suggestions ranked by edit distance. **I suggest but never auto-correct.**
  If `total_revenu` silently became `total_revenue`, the compiler would be guessing, and a wrong guess is exactly the
  silently wrong answer the brief warns about.
- Literal coercion is strict. `"2025"` is accepted for an INTEGER dimension, but `"2025x"`, `2025.5` and `true` are not. A
  date must be a real day (`2026-02-30` is refused).
- Two refusals exist only because the silent alternative is wrong. **`IdentifierTooLong`**: Postgres truncates
  identifiers over 63 bytes without an error, so two long generated names (`{alias}_{period}`) could collapse into
  one column. **`ConflictingFilter`**: a filter on the compare dimension would silently empty a period.
- I also decided not to refuse some things. A contract-level `channel = 'Online'` combined with a per-metric
  `channel = 'Retail'` returns `NULL` for that metric. That is the true answer to the question asked, and detecting
  contradictions in general is out of scope.

## 4. Adding a feature: `having` and a month-by-month trend

Every feature follows the same path. It gets a shape rule for its JSON, a typed node in the `Contract` tree, and a
module in `features/` that adds to the plan. Existing features, the join planner and the dialects are left alone.

**`having`**, e.g. "regions whose revenue grew more than 10%":
- `schema.py` gets one rule for `having: [{output, op, value}]`, and `contract.py` gets a `HavingSpec` node. Today
  the same contract fails cleanly with `unknown key 'having'`, as shown above.
- `features/having.py` resolves each `output` against the plan's output columns (so it can reference `online_rev`
  or `total_revenue_pct_change`), type-checks the literal, and adds an outer-level predicate.
- The plan gets one new field (`outer_where`), and `lower.py` renders it as a `WHERE` on the outer query. Because
  the query is **always two levels**, derived columns such as `pct_change` already exist at that level. So `having` on a
  percentage change needs no new query shape. This is the payoff of the always-two-levels design.
- There is one semantic decision to make explicitly: whether `having` also filters the totals row. I would exempt it
  (`OR is_total`), so the total still describes all the data, and write that down in the interaction table.

**Month-by-month trend**, e.g. revenue by month of `order_date`:
- A dimension gets an optional `grain` (`day | month | quarter | year`), and the contract names it
  (`"group_by": [{"dimension": "order_date", "grain": "month"}]`).
- The plan gets a `DateTrunc(column, grain)` expression that is usable as a group key, and the SQL AST gets a matching node.
- Spelling is the only dialect-specific part, and it lives in the renderer. DuckDB and Postgres both write
  `date_trunc('month', …)`, and a future dialect overrides one method.
- Totals, compare and per-metric filters need no change: a truncated date is just another group key.

## 5. What breaks first at 10× vocabulary

**The feature interaction table breaks first.** Each feature works fine on its own. The bugs live in the pairs.
`fiscal_year = 2026` is a valid filter, and 2025-vs-2026 is a valid compare, but together they silently empty the
2025 column. I only caught that by deliberately asking what filter × compare means. `DESIGN.md` §6.7 records an
answer for every pair of today's features, and every row is tested.
- About 5 features means about 10 pairs, which a person can reason through. At 10× there are hundreds of pairs, and
  nobody holds them in their head: `having` × totals, trend × compare (months or years?), top-N × compare (ranked
  by which period?), running total × totals.
- Today the rules are partly that table and partly checks scattered inside feature modules (`ConflictingFilter`
  lives in `compare.py`). That is fine at 5 features and exactly the problem at 50.
- **What I would change:** each feature declares what it produces (conditional measures, grouping sets, outer
  predicates), what it reads, and what it forbids. The compiler checks every combination at startup and raises an
  error for any pair no rule covers, so the default flips from "allowed unless listed" to "refused unless decided".
  The tests then iterate over all pairs mechanically.

**Second: metrics are one aggregate over one column.** Real vocabularies have ratios ("average order value = revenue /
orders") and metrics built from metrics. The fix is a metric dependency graph in the model (with cycle checks) and
arithmetic over measures at the outer level. The IR already does this for `delta` and `pct_change`, and it is
already correct under totals because it is row-local.

**Third: one fact table per query.** Today, metrics from two datasets raise `MultipleFactTables`. Joining two facts
directly would fan out and double-count. The right shape is to aggregate each fact separately to the shared grain,
then join the results on the conformed dimensions (with grouping sets inside each block, so totals stay correct). The
outer query already exists, so this changes the inner level from one block to several. It does not change how contracts are
understood.

**What does not break:** aggregations, operators, types and dialects are registry entries, and the speed budget has
about 50× headroom (§6).

## 6. Measured performance

`make bench` times 10,000 compiles for each contract on each dialect. The time is measured from contract dict in to SQL string out, and
includes validating the model and the contract on every call:

| Contract | p50 | p95 | max |
|---|---|---|---|
| A (per-metric filter) | 0.062 ms | 0.069 ms | 0.264 ms |
| B (compare + totals) | 0.093 ms | 0.100 ms | 0.200 ms |
| C (distinct count + totals) | 0.054 ms | 0.063 ms | 0.212 ms |
| 3 synthetic contracts (4-hop joins, up to 7 metrics) | 0.15 ms | 0.16–0.18 ms | 0.29–3.7 ms |

These are the DuckDB numbers. Postgres is within a few microseconds. Measured on an Apple M4 (10 cores), CPython 3.13.5.
- Every p95 is at least 50× under the 10 ms budget. The test suite asserts p95 < 10 ms for every case, so a
  regression fails `make test` rather than a benchmark nobody reads.
- There is no warm-up, so `max` includes the first call and the occasional garbage-collection or scheduler pause (the ~3 ms
  outliers).
- I never optimised for speed. The numbers come from the shape: one pass per stage, no search except a
  breadth-first search over a handful of relationships, and no string re-parsing. If models grow large, the obvious next step is caching
  the validated Catalog per model instead of rebuilding it on every call.
- **Determinism:** every iteration is over declared order (contract order, model declaration order, sorted
  suggestions). Tests compile the same contract 50–100 times and compare bytes, and golden files pin the exact SQL.

## 7. Tests: why I would trust a refactor

There are 629 tests (`make test`, about 3 s), plus 75 more that run on a real Postgres 16 (`make test-pg`).
- **End to end:** A, B and C run on DuckDB *and* Postgres and match the PDF's expected results. The Postgres tests reuse
  the same expectations.
- **Golden SQL:** exact snapshots per contract and dialect, which catch accidental output changes.
- **Generality:** a second, synthetic model (bike-share: 7 datasets, 4-hop joins, all 7 types, all 6 aggregations,
  physical column ≠ public name) that shares no names with the PDF's model. Its 150 tests were written from
  `DESIGN.md` alone, without reading the implementation, with every expected value computed by hand from the seed rows.
- **Feature interactions:** every allowed row of the interaction table is executed on real data, and every forbidden
  row is asserted as its named error.
- **Edge cases and errors:** empty results, NULL group keys vs the total row, missing join keys at each hop, zero
  bases, identifier limits in bytes (`ü` counts as 2), and the error types with their paths and suggestions.
- **Units:** the registries, literal coercion, the join planner (diamond paths, fan-out refusal) and the renderer's precedence
  and parenthesisation.

## 8. What I cut, and what comes next

Each of these would be a new module or registry entry. None of them needs a rewrite:
- **Subtotals / rollups:** more entries in `grouping_sets`, with `is_total` generalising to a grouping-level column.
- **OR / NOT in contract filters:** the IR already has them, so only the parser changes.
- **Comparing more than two periods:** needs a defined rule for what `delta` means across three or more periods.
- **Role-playing dimensions** (order date vs ship date): named relationships, which are refused today as `AmbiguousJoinPath`.
- **Showing groups that have no facts:** start from the dimension's members and LEFT JOIN the aggregated facts onto them.
- **Snowflake:** a third dialect with `supports_aggregate_filter = False`. The CASE WHEN path already exists and is tested.

Known caveats:
- Text ordering follows each engine's collation, so mixed-case keys can sort differently on Postgres than on DuckDB.
- Postgres returns some numerics as `Decimal` where DuckDB returns `float`.
- The CLI's default seed is found relative to the repository, so it is not packaged.

## 9. How this was built

I used Claude Code (an AI coding assistant) throughout, in a deliberately constrained way:
- **Design first.** I settled the core design through a question-and-answer design review before any code existed, and
  locked it in `DESIGN.md`. Any later change (the zero-base rule, the count rule,
  `IdentifierTooLong`) was recorded there before it was implemented.
- **Build in small slices** (`PLAN.md`). Each slice started with failing tests and ended green, as one commit.
- **Separate test writer and implementer** (`.claude/agents/`). One agent wrote only tests, and for the synthetic model
  it worked from the spec alone, never reading the code. Another agent wrote only the implementation and was
  not allowed to change an expectation. I reviewed every slice at two gates, once for the tests and once for the code.

The split exists so that tests check the specification rather than echo the implementation. It is also how
two real gaps surfaced: the count-vs-NULL rule and the zero-base division.
