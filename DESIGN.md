# Design: Query Contract → SQL Compiler

Status: **Locked** (2026-09-26). Implementation follows this document; any deviation gets recorded here first.

## 1. Goal

One pure function:

```python
compile_contract(semantic_model: dict, contract: dict, dialect: str,
                 options: CompileOptions | None = None) -> str
```

- Output is a SQL string for `duckdb` or `postgres`.
- `options` is optional and only tunes error reporting (the fuzzy-suggestion threshold, §9). It never changes the emitted SQL.
- Deterministic: same input → byte-identical SQL, every run, every process.
- Under 10 ms per contract (contract in → SQL string out), measured and reported.
- No I/O, no network, no LLM, no randomness, no reading the warehouse.
- Anything unknown or unsupported raises a named `CompilerError`, never a best guess.

## 2. Generality principles

The PDF's model and contracts A/B/C are **fixtures, not the specification**. The compiler must work for
any semantic model and any contract written in the vocabulary of §5.

1. **Zero hardcoded names.** No code path ever mentions `fact_sales`, `revenue`, `region`,
   `fiscal_year` or any other model name. Every table, column, type and join comes from the semantic model.
2. **Features compose; they are not special-cased.**
   - Each contract feature is a small expansion into a few general IR building blocks: conditional measures,
     predicates, grouping sets and derived outputs.
   - Any combination of features works unless §6.7 explicitly forbids it. Examples: compare combined with per-metric filters,
     totals over several group_by dimensions, or `count_distinct` inside a compare.
3. **Registries instead of `if` chains.**
   - Aggregations, filter operators, dimension types and dialects are each a table of entries.
   - Adding one means adding an entry, not editing the compiler's control flow.
4. **Any graph shape the model can express safely.**
   - Star and snowflake schemas and multi-hop joins all work.
   - Joins are found by searching the declared relationships, not by matching known table names.
5. **Proven by a second model.** The test suite includes a synthetic semantic model that shares no names with the PDF's.
   It has a different domain, a multi-hop snowflake dimension, several dimension types, and every aggregation. The same
   tests run against both models (§10).

## 3. Pipeline

```
SYNTACTIC ANALYSIS
  semantic_model ─▶ schema.py (shape rules) ─▶ model.py    ─▶ Catalog (symbol table: names, types, join edges)
  contract ───────▶ schema.py (shape rules) ─▶ contract.py ─▶ Contract tree (typed)
                                          │
SEMANTIC ANALYSIS                         ▼
                   resolve.py + features/* ─▶ LogicalPlan (IR)      ◀─ knows contracts, knows nothing about SQL text
                                          │
LOWERING                                  ▼
                                  lower.py ─▶ SQL AST               ◀─ knows SQL structure, not dialect spelling
                                          │
RENDERING                                 ▼
                 dialects/{duckdb,postgres}.py ─▶ str               ◀─ knows spelling, nothing about contracts
```

**The two analysis phases:**

1. **Syntactic analysis** asks whether the input is well-formed, independent of any customer model.
   - Both JSONs are checked against **declarative shape rules** in `schema.py`: allowed keys, required fields, value
     kinds (string / number / list / object), allowed enum values (`totals`, compare `outputs`) and operator arity.
   - The rules are data, not hand-written `if`s. A new contract key is one new rule entry.
   - For the contract, the result is a typed tree (`Contract`).
   - For the model, the result is the **Catalog**: a symbol table of datasets, metrics, dimensions (with types and
     columns) and followable join edges. Its keys are the "acceptable values" for every name-bearing node in the
     contract.
2. **Semantic analysis** (`resolve.py`) asks whether the tree means something in *this* Catalog, and what exactly.
   - It looks up every name in the Catalog.
   - It type-checks every literal and every operator.
   - It finds the join path for every column that lives on a different dataset than the base. This covers the indirect
     relationships: e.g. `fiscal_year` is reached through `fact_sales.order_date = dim_calendar.date`, and `region`
     through `fact_sales.store_id = dim_store.store_id`.
   - It records that path on the resolved column reference.

**Where the dialect line is drawn:** everything above the renderer is dialect-free. A new dialect is a new
`Dialect` subclass (quoting, literals, type names, aggregate-filter spelling, capability flags) and never
touches `contract.py`, `resolve.py`, `features/` or `lower.py`.

**How resolve is structured:** `resolve.py` builds a `PlanBuilder` step by step, in a fixed order:

1. metrics
2. filters
3. each feature's expansion, in `features/`
4. joins
5. output naming and validation

Each feature module takes the parsed contract section plus the builder and adds measures, predicates, grouping
sets or outputs. A new contract feature is a new module in `features/` and never an edit to the others.

## 4. Module layout

```
walt_compiler/
  __init__.py        # compile_contract() — the only public entry point
  errors.py          # CompilerError hierarchy
  suggest.py         # Levenshtein "did you mean" suggestions (error path only)
  schema.py          # declarative shape rules for semantic model + contract
  types.py           # dimension-type registry + literal coercion
  aggregations.py    # aggregation registry
  operators.py       # filter-operator registry
  model.py           # semantic model → Catalog (cross-reference checks)
  contract.py        # contract JSON → typed Contract tree
  joins.py           # join-graph search over declared relationships
  plan.py            # LogicalPlan IR + PlanBuilder (frozen dataclasses)
  resolve.py         # orchestrates Contract + Catalog → LogicalPlan
  features/
    metric_filters.py  # per-metric filters → conditional measures
    compare.py         # compare → per-period conditional measures + derived outputs
    totals.py          # totals → grouping sets + is_total output
  sqlast.py          # SQL AST dataclasses (frozen)
  lower.py           # LogicalPlan → SQL AST
  dialects/
    __init__.py      # dialect registry
    base.py          # Dialect interface + shared rendering
    duckdb.py
    postgres.py
  __main__.py        # CLI: `compile` prints SQL; `run` executes on DuckDB and prints a table
fixtures/
  pdf/               # the PDF's model, schema (DuckDB + Postgres DDL), contracts A/B/C
  synthetic/         # second model with no shared names, used to prove generality
  orphan_rows.sql    # extra rows for join-semantics tests
tests/
bench/
docker-compose.yml   # Postgres 16 for dialect tests
Makefile             # make run | make test | make bench
```

The compiler itself uses only the standard library. `duckdb`, `psycopg` and `pytest` are needed only for running and testing.

### CLI

```
python -m walt_compiler compile --model M.json --contract C.json --dialect duckdb|postgres
python -m walt_compiler run     --model M.json --contract C.json [--seed schema.sql]
```

- `compile` prints the SQL string. On a `CompilerError` it prints the code, path, message and suggestions to stderr
  and exits non-zero.
- `run` compiles for DuckDB, loads the seed SQL into an in-memory DuckDB (default: the PDF fixture), executes the
  query, and prints the result as a table (header row of column names, then one line per row).
- The CLI does **no data validation** of warehouse contents. It only validates the input JSONs, through the compiler.

## 5. Semantic model (generic contract with the customer)

The PDF's model format is extended with fields that are **optional or defaulted**, except for a dimension's `type`.

### Datasets
`{ "name", "grain" }`
- Names must be unique.
- `grain` is documentation only.

### Relationships
`{ "from", "from_column", "to", "to_column", "cardinality" }`
- Both endpoints must be declared datasets.
- Cardinality `many_to_one` or `one_to_one` can be followed from `from` to `to`.
- `one_to_many` and `many_to_many` are valid to declare, but a query that needs one raises `UnsupportedRelationship`.
  They would fan out fact rows and inflate aggregates.
- Relationships are never walked backwards, because a reversed `many_to_one` is `one_to_many`.
- Join keys are single-column equality only.

### Metrics
`{ "name", "agg", "expression", "model", "measure_class" }`
- `expression` is a column name on `model`. Arbitrary SQL expressions are deliberately excluded: they would
  be dialect-specific and could not be validated.
- `agg` must be in the aggregation registry.
- `measure_class` must be consistent with `agg` (e.g. `count_distinct` ↔ `distinct_count`), otherwise it raises `InvalidSemanticModel`.
- Names are unique across metrics.

### Dimensions
`{ "name", "model", "type", "column"? }`
- **`type` is required.** It must come from the type registry: `text`, `integer`, `double`, `decimal`, `date`, `timestamp`, `boolean`.
- `column` defaults to `name`, which lets a dimension's public name differ from the physical column.
- Names are unique across dimensions.
- Why types are required: the model is the only source of truth and we never read the warehouse. Without types,
  `"2025"` cannot be rendered correctly against an INTEGER column, and date literals cannot be written correctly.

### Cross-reference checks (Catalog build)
- Every `metric.model`, `dimension.model` and relationship endpoint must name a declared dataset.
  - Note that `dimension.model` is a **dataset name**, while `dimension.name` is the public dimension name and
    `dimension.column` is the physical column. These are three different namespaces and are never confused.
- Dataset, metric and dimension names are each unique within their own namespace.
- A failure raises `InvalidSemanticModel`, with the JSON path into the model and fuzzy suggestions (§9).

The whole model is validated on every call, which costs microseconds at this size. Caching per model is a later optimization.

### Registries

| Registry | Entries (initial) | Entry defines |
|---|---|---|
| Aggregations | `sum`, `count`, `count_distinct`, `min`, `max`, `avg` | SQL function, DISTINCT flag, allowed argument types, whether NULLs are ignored (required for the CASE fallback in §8) |
| Operators | `=`, `!=`, `<`, `<=`, `>`, `>=`, `in` | arity (scalar/list), allowed types (no `<` on boolean), IR predicate constructor |
| Types | see above | literal coercion from JSON, canonical rendering form |
| Dialects | `duckdb`, `postgres` | spelling + capability flags |

All aggregations are correct under totals, because grouping sets recompute every aggregate against the base rows (§6.4).

## 6. Contract vocabulary and semantics

| Key | Shape |
|---|---|
| `metrics[]` (non-empty) | `name`, `as`?, `filters[]`? |
| `group_by[]` | any declared dimension names, no duplicates; default `[]` |
| `filters[]` | `{field, op, value, model?}` |
| `compare`? | `{dimension, periods[2], primary, outputs[]}` |
| `totals`? | `"grand"` |

**Strictness:**
- Unknown keys at any level raise `InvalidContract`.
- If a filter's `model` is present, it must equal the dimension's declared model (`ModelMismatch`).
- The operator must exist (`UnsupportedOperator`) and allow the dimension's type.
- Literals are coerced to the declared type, and a failure raises `InvalidLiteral`.
- Multiple filters in a list are ANDed together. The IR supports `And`/`Or`/`Not`, so adding OR later changes only the
  contract parser, not the rest of the compiler.

### 6.1 Joins
- The **base dataset** is the `model` of the requested metrics. Metrics on different datasets raise
  `MultipleFactTables` (§11).
- Join paths are found by BFS from the base over followable relationships (§5). Multi-hop paths are
  supported. Neighbours are visited in declaration order, so the result is deterministic.
  - Two distinct shortest paths to the same dataset raise `AmbiguousJoinPath`. Role-playing dimensions (e.g. order date vs
    ship date) need named relationships, see §11.
  - No path raises `NoJoinPath`.
- **Only the datasets the query actually needs are joined**, whether for group_by, filters, compare or per-metric filters.
- **Join type: always `LEFT JOIN`.** Attaching a dimension never drops a fact row. A fact whose key has no
  match in the dimension still counts, with NULL dimension values.
  - `INNER` is never emitted.
  - `RIGHT`/`FULL` are not needed: showing groups that have no facts is not in the vocabulary, see §11.
- **Dimension filters go in `WHERE`.** `LEFT JOIN … WHERE dim.col <op> x` genuinely restricts rows.
- **NULL semantics are standard SQL**, for every operator:
  - A fact with an unknown (NULL) dimension value matches no comparison, `!=` included.
  - `!=` is **not** rewritten to `IS DISTINCT FROM`, so all operators behave the same way.
  - The README documents this.

### 6.2 Per-metric filters → conditional aggregation
- Written as `AGG(expr) FILTER (WHERE …)`.
- Dialects without `FILTER` use `AGG(CASE WHEN … THEN expr END)`. This is only valid for aggregations whose registry
  entry says they ignore NULLs, which is all of the initial ones. Any other combination raises `UnsupportedFeature` instead of
  producing a wrong answer.
- Per-metric filters may reference any dimension of any reachable dataset. Their joins are planned like every other join.
- The same metric may appear any number of times with different aliases and filters.
- **Filter over filter:** a contract-level filter (`WHERE`) and a per-metric filter (`FILTER`) combine as AND.
- **Contradictions are not detected.** For example, a contract-level `channel = 'Online'` with a per-metric
  `channel = 'Retail'`. The emitted SQL returns NULL for that metric, and that is the true answer to the question
  asked, not a silent lie. Detecting contradictions in general is out of scope.

### 6.3 Compare
Compare is a general pivot over **any** declared dimension of **any** type.
- Exactly 2 distinct periods, coerced to the dimension's type. `primary` must be one of them. Otherwise it raises `InvalidCompare`.
- `compare.dimension` must not appear in `group_by` (`InvalidCompare`).
- `outputs` ⊆ {`values`, `delta`, `pct_change`}, non-empty, no duplicates.
- For every metric, whether aliased and/or filtered, the expansion is:
  - one conditional measure per period, whose condition is the metric's own filter AND `dimension = period`
  - an implied contract-level `WHERE dimension IN (p1, p2)`
  - derived outputs: `{out}_delta = primary − other` and `{out}_pct_change = 100.0 * (primary − other) / other`.
    This is a percentage, not rounded, since rounding is presentation.
- The column name is `{out}_{period}`, where `{period}` is the period exactly as written in the contract. Identifiers are always
  quoted, so any characters are legal. Name clashes are caught in §6.5.
- **Division by zero is left to the dialect.** The compiler emits SQL and does not judge the data. Dialect-specific
  tests pin each behaviour down.
- If a group has no rows for a period, the value is `NULL`, not 0. No data is invented.

### 6.4 Totals
- `totals: "grand"` with `group_by = (g1…gn)` becomes `GROUP BY GROUPING SETS ((g1, …, gn), ())`.
- Every aggregate on the totals row is **recomputed against the base rows**, for every aggregation. That is why a distinct count
  totals correctly (Contract C: 8, not 9) and why an average of averages never happens.
- Derived columns (delta, pct) are computed in the outer query, so the totals row uses its own cells
  (Contract B: 57.8% = 260 / 450).
- The totals row keeps group columns as `NULL` and adds **`is_total BOOLEAN`**, from `GROUPING(g1) = 1`. With
  only the full set and the empty set, `g1` alone identifies the totals row. A NULL group value from missing
  dimension data stays distinguishable from the total.
- `is_total` is present only when totals are requested.
- `totals` with an empty `group_by` raises `InvalidContract`. The ungrouped row already is the grand total.
- Any other value (e.g. subtotals) raises `UnsupportedFeature`.

### 6.5 Output shape, naming and ordering
- Column order:
  1. group_by columns, in contract order
  2. metrics in contract order, each with its values then delta then pct_change
  3. `is_total`
- All output names must be unique, including generated compare names and `is_total`. A clash raises
  `DuplicateOutputName`.
- `ORDER BY is_total ASC, g1 ASC NULLS LAST, …, gn ASC NULLS LAST` is emitted whenever `group_by` is non-empty.
  - `NULLS LAST` is always spelled out explicitly.
  - Without group_by the result is one row, so there is nothing to order.

### 6.6 Query shape
- **Always two levels**: an inner aggregate query, and an outer projection that computes derived columns and does the ordering.
- There is one code path for every contract.

Example: Contract B rendered for DuckDB:

```sql
SELECT "region", "total_revenue_2025", "total_revenue_2026",
       "total_revenue_2026" - "total_revenue_2025" AS "total_revenue_delta",
       100.0 * ("total_revenue_2026" - "total_revenue_2025") / "total_revenue_2025" AS "total_revenue_pct_change",
       "is_total"
FROM (
  SELECT "dim_store"."region" AS "region",
         SUM("fact_sales"."revenue") FILTER (WHERE "dim_calendar"."fiscal_year" = 2025) AS "total_revenue_2025",
         SUM("fact_sales"."revenue") FILTER (WHERE "dim_calendar"."fiscal_year" = 2026) AS "total_revenue_2026",
         GROUPING("dim_store"."region") = 1 AS "is_total"
  FROM "fact_sales"
  LEFT JOIN "dim_store"    ON "fact_sales"."store_id"   = "dim_store"."store_id"
  LEFT JOIN "dim_calendar" ON "fact_sales"."order_date" = "dim_calendar"."date"
  WHERE "dim_calendar"."fiscal_year" IN (2025, 2026)
  GROUP BY GROUPING SETS (("dim_store"."region"), ())
) AS "agg"
ORDER BY "is_total" ASC, "region" ASC NULLS LAST
```

(Line breaks are illustrative. The real renderer's layout is fixed and covered by snapshot tests.)

- Inner-level aliases for measures are generated from output names, so they are unique and deterministic.
- Dataset names serve as table aliases. They are unique by model validation and appear only once per query, because
  every dataset is joined at most once.

### 6.7 Feature interaction rules

Every combination is allowed except the ones listed here.

| Combination | Result |
|---|---|
| compare + per-metric filters | ✅ period condition AND metric filter |
| compare + totals | ✅ (Contract B) |
| compare + multiple metrics / multiple group_by | ✅ |
| compare + any aggregation | ✅ |
| totals + any aggregation / multiple group_by | ✅ |
| same metric repeated with different aliases/filters | ✅ (Contract A) |
| filter (contract-level or per-metric) on the compare dimension | ❌ `ConflictingFilter`: it would silently empty a period |
| compare dimension also in `group_by` | ❌ `InvalidCompare` |
| totals with empty `group_by` | ❌ `InvalidContract` |
| metrics from different datasets | ❌ `MultipleFactTables` |

## 7. IR (plan.py)

All IR types are frozen dataclasses holding tuples, never sets or dicts that get iterated for output.

```
LogicalPlan
  base: DatasetRef
  joins: tuple[JoinStep]              # (from_dataset, from_col, to_dataset, to_col), in BFS order
  group_by: tuple[ColumnRef]          # ColumnRef = (dataset, column, type)
  measures: tuple[Measure]            # inner-level aggregates
  where: Predicate | None
  grouping_sets: tuple[tuple[ColumnRef]] | None   # None = plain GROUP BY
  outputs: tuple[OutputColumn]        # outer-level projection
  order_by: tuple[OrderKey]

Measure        = (alias, agg: AggregationSpec, arg: ColumnRef, condition: Predicate | None)
Predicate      = Compare(col, op, TypedLiteral) | InList(col, tuple[TypedLiteral])
               | And(tuple[Predicate]) | Or(tuple[Predicate]) | Not(Predicate)
OutputColumn   = (name, expr: MeasureRef | GroupRef | Sub | PctChange | IsTotal)
TypedLiteral   = (value, type)
```

`grouping_sets` is general. Subtotals or rollups later are more sets, not a new concept.

## 8. SQL AST (sqlast.py) and dialects

- AST nodes: `Select`, `Subquery`, `Join`, `Column`, `Aggregate(func, arg, distinct, filter)`, `BinOp`,
  `UnaryOp`, `Literal`, `InList`, `Grouping`, `GroupingSets`, `OrderItem`, `Alias`.
- `Dialect` interface:
  - `quote_ident(name)`: always quotes, so reserved words like `date` are safe
  - `render_literal(TypedLiteral)`: per type; escapes strings, `DATE '…'`, `TIMESTAMP '…'`, `TRUE/FALSE`,
    canonical number form
  - `supports_aggregate_filter`, `supports_grouping_sets`: capability flags
  - `render_aggregate(...)`
- Literals are always rendered by the dialect. A contract value is never pasted into SQL text.
- Honest note: DuckDB and Postgres are close for this vocabulary. The differences live in the fixtures (DDL types) and in
  runtime behaviour (division by zero) more than in the emitted text. The seam still exists and is tested, and
  a `FILTER`-less dialect goes through the capability flag, not through the contract layer.

## 9. Errors (errors.py)

All errors derive from `CompilerError`, which has `code`, a human-readable `message` and a JSON-pointer `path` into the contract
or model (e.g. `/metrics/0/filters/0/op`).

`InvalidSemanticModel`, `InvalidContract`, `UnknownDialect`, `UnknownMetric`, `UnknownDimension`,
`UnsupportedOperator`, `UnsupportedAggregation`, `UnsupportedFeature`, `UnsupportedRelationship`,
`InvalidLiteral`, `ModelMismatch`, `DuplicateOutputName`, `NoJoinPath`, `AmbiguousJoinPath`,
`MultipleFactTables`, `InvalidCompare`, `ConflictingFilter`.

### "Did you mean" suggestions
- `UnknownMetric`, `UnknownDimension`, `UnknownDialect`, `UnsupportedOperator`, `UnsupportedAggregation`, and unknown
  keys under `InvalidContract` / `InvalidSemanticModel` carry a `suggestions` list.
- Candidates are the names in the relevant Catalog namespace or registry.
- The score is Levenshtein distance ÷ the length of the longer string, compared case-insensitively.
- A candidate is suggested when its score is ≤ `options.suggestion_threshold` (default **0.4**). At most
  `options.max_suggestions` (default **3**) are returned.
- Suggestions are sorted by (score, name), so error messages are deterministic too.
- **Suggest, never auto-correct.** The compiler still fails. Silently picking the closest name would be a guess.
- The Levenshtein implementation is our own (~15 lines) and runs only on the error path, so it costs nothing on the happy path.

Example: `UnknownMetric at /metrics/0/name: 'total_revenu' is not a metric. Did you mean: total_revenue?`

## 10. Test plan (`make test`)

| Suite | What it proves |
|---|---|
| PDF fixtures A/B/C, DuckDB + Postgres | Compile, execute, and compare against the PDF's expected results (float tolerance for pct) |
| **Synthetic model** | A second model with no shared names: multi-hop snowflake join, all dimension types (date/timestamp/boolean literals), every aggregation, and dimensions with `column` ≠ `name`. Results are checked against hand-computed expectations. |
| **Feature combinations** | Every ✅ row of §6.7 executed on real data; every ❌ row asserted as its error |
| Join semantics | Orphan fact rows appear with NULL dimension values and in totals; a dimension filter excludes them; `!=` excludes them (§6.1) |
| Fan-out guard | A query that needs a `one_to_many` relationship raises `UnsupportedRelationship`; `one_to_one` is joined |
| Totals | Distinct-count and average totals are recomputed, not summed; NULL group vs `is_total` are distinguished |
| Dialect-specific | Division by zero: Postgres raises an error, DuckDB's native result is pinned |
| Golden SQL | Snapshot of exact SQL per contract × dialect, which catches accidental output changes |
| Determinism | 100 compiles give identical output; also identical across subprocesses with different `PYTHONHASHSEED` |
| Errors | One test per error code, asserting code and path |
| Suggestions | Typos yield the expected suggestions in deterministic order; distant names yield none; threshold option respected; nothing is ever auto-corrected |
| Shape rules | Missing required keys, wrong value kinds, unknown keys and bad enum values in both model and contract |
| Model cross-references | Dimension/metric pointing at an undeclared dataset, duplicate names, relationship to an unknown dataset |
| CLI | `compile` prints SQL and exits 0; an invalid contract exits non-zero with the error on stderr; `run` prints the expected table for Contract A |
| Performance | p50 / p95 / max over 10k compiles per contract, asserted under 10 ms (numbers go in the README) |

## 11. Out of scope (cut), and where each would go

- **`having`**: a new `features/having.py` that adds a predicate over outer-level output columns and renders as a
  `WHERE` in the outer select. Needs a decision on whether it applies to the totals row.
- **Month-by-month trend**: a time-grain dimension (`order_date` at `month`). Needs a `grain` on dimensions, a
  `DateTrunc` IR expression, and per-dialect spelling in the renderer.
- **Subtotals / rollups**: more entries in `grouping_sets`. `is_total` generalizes to a grouping-level column.
- **OR / NOT in contract filters**: the IR already supports them, so only a parser change is needed.
- **N-period compare**: needs a defined rule for delta with more than two periods.
- **Multiple fact tables**: aggregate each fact separately to the shared grain, then join on conformed dimensions,
  to avoid chasm/fan traps.
- **Role-playing dimensions**: named relationships, with a dimension choosing its relationship.
- **Showing groups that have no facts**: take the dimension's members and LEFT JOIN the aggregated facts onto them.
- **Composite / non-equality join keys**: deliberately not supported (decided). It would need
  `from_columns`/`to_columns` lists in the model and an AND-ed `ON` clause.
- **Warehouse data validation in the CLI** (e.g. orphan-key reports): deliberately not supported (decided).
- **Contradictory-filter detection**: deliberately not supported (decided, §6.2).
- **Snowflake**: a third `Dialect` subclass with `supports_aggregate_filter = False`.

## 12. Build order (1 day)

1. errors, registries, model, contract parsing, then resolve → plan → lower → DuckDB renderer. Get A, B and C passing on DuckDB.
2. Synthetic model + feature-combination tests. These are the proof of generality, so they come before polishing anything.
3. Error-path tests and determinism tests.
4. Benchmark script and numbers.
5. Postgres renderer, docker-compose, Postgres tests, and the division-by-zero tests.
6. README (reasoning, extensions, 10× growth, timings, cuts). Budget about 2 hours for it.

## 13. Test-writing process

- The tests that go beyond the PDF (synthetic model, feature combinations, edge cases) are written by a **separate
  agent**.
  - It works only from this DESIGN.md and the PDF, **never from the implementation**.
  - It computes every expected value by hand from the fixture data.
- It then runs the tests and fixes things in a loop until everything passes. The rule for that loop: **fix the code, never the
  expectations.**
  - Changing any expected value, or deleting a test, needs the author's explicit approval, with the reason written down.
  - This stops the loop from "passing" by weakening the tests.
- If a test shows this design is ambiguous or wrong, the design is corrected here first. Code and tests then follow
  it.
