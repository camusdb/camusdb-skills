# Reading CamusDB EXPLAIN output

`EXPLAIN` output is for people. The docs give no format stability promise, so
do not parse it in production code.

## Forms

```sql
EXPLAIN SELECT ...              -- physical plan, no rows read
EXPLAIN (PHYSICAL) SELECT ...   -- identical to plain EXPLAIN
EXPLAIN (LOGICAL) SELECT ...    -- same tree, stage column = 'logical'
EXPLAIN (ANALYZE) SELECT ...    -- executes and drains the query, adds counters
EXPLAIN SELECT ... UNION SELECT ...          -- a set operation
EXPLAIN SELECT ... FOR UPDATE [NOWAIT | SKIP LOCKED]   -- a locking read, in every form
```

- Only these three option words exist. Any other word is an error.
- `EXPLAIN` accepts a `SELECT` statement only. A set operation and `FOR UPDATE`
  count as a `SELECT`. `UPDATE`, `DELETE` and `INSERT` do not.
- An uncorrelated subquery executes once during planning, even for plain
  `EXPLAIN`.
- The join planner can read a small filtered dimension table during planning, even
  for plain `EXPLAIN`. See the plan-time lookup in
  [planner-rules.md](planner-rules.md).
- Plain `EXPLAIN ... FOR UPDATE` takes no lock. It needs `SELECT` and `UPDATE` on
  the table. It also works in a read-only or optimistic transaction.
- `EXPLAIN (ANALYZE) ... FOR UPDATE` runs the statement and takes real row locks.
  They stay until the transaction ends. Every check of the statement applies, and
  `NOWAIT` can fail it with `CADB0555`. In autocommit it runs in a writable
  transaction.
- `EXPLAIN` without `ANALYZE` adds no count to `SHOW INDEX USAGE`.
  `EXPLAIN (ANALYZE)` adds counts.
- A `SELECT` without `FROM` and without `WHERE` renders `constant-source`,
  `project`, and `limit` when present. It has no costed plan, and
  `EXPLAIN (ANALYZE)` rejects it. Plain `EXPLAIN` of this form does not run the
  projection subqueries.
- A `SELECT` without `FROM` but with `WHERE` plans a `function-scan` of
  `*single_row*`. `EXPLAIN (ANALYZE)` works on it and runs the filter and the
  projection.

## Result columns

Plain `EXPLAIN`:

| Column | Meaning |
| --- | --- |
| `stage` | `physical` or `logical`. `EXPLAIN (ANALYZE)` rows show `analyze`. |
| `node` | Canonical node name. No indentation. |
| `detail` | Key facts of the node. |
| `estimated_rows` | Cost-model output cardinality. `NULL` when not costed. |
| `estimated_cost` | Unitless weighted cost. Lower is cheaper. `NULL` when not costed. |

`EXPLAIN (ANALYZE)` adds:

| Column | Meaning |
| --- | --- |
| `actual_rows` | Rows this operator gave its parent. |
| `rows_read` | Rows a scan fetched and decoded before filtering. `0` on pipeline operators. |
| `actual_time_ms` | Total wall-clock time of the whole plan. Root row only; `NULL` elsewhere. |
| `kv_lookups` | Point lookups on a unique index. |
| `kv_scan_entries` | KV entries visited by a table scan or index range scan. |
| `estimate_trace` | JSON: how the row estimate was made. The formula, the rule (such as `equality-heavy`, `like-prefix`, `join-skew-pair`), input rows, and each factor with the statistics it read. `NULL` when the node has no estimate. |

On a scan, `actual_rows <= rows_read`. The gap is rows rejected by the filter
folded into the scan.

## Row order

Rows come depth-first, parent before child. The first row is the root. Read
from the last row upward to follow data flow.

## Operator nodes

| Node | Appears for | Detail |
| --- | --- | --- |
| `table-scan` | Full table scan, or an end-to-end scan of a forced index | `table=<name>`; forced adds `, forced-index=<name>` |
| `index-lookup` | Equality on a unique index (`~pk` or `UNIQUE`) | `index=<name>, key=<value>`; covered adds `, index-only=true` |
| `index-range-scan` | Range predicate, `BETWEEN`, equality on a non-unique index, or an ordered index read | `index=<name>, from>=<v>, to<<v>`; covered adds `index-only=true` |
| `index-in-list` | `col IN (v1, v2, ...)` on an indexed column | `index=<name>, values=<n>` (distinct values probed) |
| `gin-scan` | A JSONB predicate served by a GIN index. A `filter` above it checks each candidate | `index=<name>, groups=<n>, entries=<m>` |
| `filter` | Residual predicate not served by the index | `<expr>` |
| `aggregate` | `GROUP BY` or an aggregate function | `group=[...], aggs=[...]`; a grouped top k adds `top_k=[<alias> ASC/DESC, k: <n>]` |
| `having-filter` | `HAVING` | `<expr>` |
| `sort` | `ORDER BY` not satisfied by scan order, no usable bound | `<col> ASC/DESC, ...` |
| `topk` | `ORDER BY` bounded by `LIMIT`; keeps `offset + limit` rows. With spill on, it moves to disk only when those rows pass the byte cap | `k: <n>, <col> ASC/DESC, ...` |
| `lock_rows` | `SELECT ... FOR UPDATE`. Below `limit`, above the sort. Locks each candidate, rereads it, rechecks `WHERE` | `for update`, `for update, nowait` or `for update, skip locked` |
| `limit` | `LIMIT` / `OFFSET` | `<n>` or `<n> offset <m>` |
| `project` | Final projection | none |
| `distinct` | `SELECT DISTINCT` | `streaming: true` or `hash` |
| `semi-join` | `IN (subquery)` over an indexed inner column | `outer=<col>, inner=<table>.<col>, index=<name>` |
| `anti-join` | `NOT IN (subquery)`, indexed inner column that is `NOT NULL` | same as `semi-join` |
| `null-aware-anti-join` | `NOT IN (subquery)`, indexed nullable inner column | same as `semi-join` |
| `nested-loop-join` | Join without a usable right index or equality key | `on=<expr>, right=<alias>` |
| `index-nested-loop-join` | Right side has an index on the join key | `on=<expr>, index=<name>, left=<col>, right=<col>` |
| `hash-join` | Equi-join with an in-memory hash table | `on=<left>=<right>, build=<alias>` |
| `merge-join` | Equi-join over ordered inputs | `on=<l>=<r>, right-order=forced-index=<name>` or `right-order=sort` |
| `derived-table-scan` | Subquery in `FROM`, or an expanded CTE | `alias=<alias>`; a shared CTE result adds `computed_once=<name>` |
| `recursive-union` | A reader of a `WITH RECURSIVE` CTE. The seed and recursive-term plans follow it | CTE name and reader alias |
| `work-table-scan` | The rows of the previous recursive step | The CTE work table |
| `values` | A `VALUES` list used as the sole source | `alias=<name>, rows=<n>` |
| `window` | Window functions, one entry per distinct `(PARTITION BY, ORDER BY)` | `partition=[...] order=[...] functions=[...]` |
| `function-scan` | Set-returning function in `FROM`, such as `generate_series`. No table access. Constant arguments give an exact `estimated_rows` | `<function>(<args>), alias=<alias>, column=<column>` |
| `union` | `UNION [ALL]` | `all`, or `distinct: hash` |
| `intersect`, `except` | `INTERSECT [ALL]`, `EXCEPT [ALL]` | `hash`, or `all, hash` |
| `operand` | One operand of the set operation above it. The rows after it are that operand's plan | `left` or `right` |
| `constant-source` | `SELECT` without `FROM` | `1 row` |
| `gather` | Distributed scan split by span | `spans: <n>, remote_leader_fraction: <f>` |

Range bound rendering: numeric types show a half-open range
(`from>=2023, to<2024`). An equality with no computable next value shows
`from>=v, to<=v`. That covers `STRING`, `OID`, `DATE`, `DATETIME`, `FLOAT32` and
`BYTES` columns, a value at its type's maximum, and a descending column. Both forms
return the same rows.

On a set-operation row, `, spill` follows `hash` when spill is enabled. It means the
hash table can move to disk, not that it did. `estimated_rows` on that row is the
default derived-table estimate. The planner does not estimate the size of a set
operation.

A FROM-less operand of a set operation shows a one-row
`function-scan(*single_row*(), ...)`.

### Grouped top k

A grouped query ordered by one aggregate alias, with a `LIMIT`, can add `top_k` to
the `aggregate` detail. `k` is `OFFSET + LIMIT`:

```sql
EXPLAIN SELECT customer_id, MAX(total) AS largest_order
FROM orders GROUP BY customer_id
ORDER BY largest_order DESC LIMIT 5;
-- aggregate detail includes: top_k=[largest_order DESC, k: 5]
```

- Eligible: `COUNT` descending, `MAX` descending, or `MIN` ascending on a
  `NOT NULL` column. No `HAVING`.
- `MAX DESC` or `MIN ASC`, with only grouped columns and the ordered aggregate
  projected: each cluster span sends only its local top k groups, ties included.
- `COUNT DESC` with spill: a spilled grouping can skip spill partitions whose
  upper bound cannot beat the current k-th count. It still scans the whole input.
- The result does not change. Ties come back in no fixed order. Add `ORDER BY` keys
  when you need a deterministic order.

### CTE and recursive plans

- A CTE read once is inlined. Its reader shows a `derived-table-scan`.
- A CTE read more than once is computed once. Each reader shows
  `computed_once=<name>`. The CTE runs in full before a reader gets a row, even
  under `LIMIT`.
- A `WITH RECURSIVE` reader shows `recursive-union`, then the seed plan and the
  recursive-term plan with a `work-table-scan`. `EXPLAIN (ANALYZE)` does not count
  the rows of each term separately.
- A `VALUES` list that is the only source shows `values(alias=v, rows=N)`.

### Extra detail suffixes on join rows

The engine renderer (`PlanRenderer.cs`) emits these suffixes.

| Suffix | Meaning |
| --- | --- |
| `kind=left-outer` | `LEFT JOIN`, or a `RIGHT JOIN` swapped into a left join |
| `lateral` | The right source is a `LATERAL` source. It runs as a nested loop, in the written join order |
| `right-filter=<expr>` | Filter pushed to the right input of a nested-loop, index nested-loop or merge join |
| `build-filter=<expr>` | Filter pushed to the hash build side |
| `right-index=<name>` | Right input of a hash or nested-loop join reads an index range |
| `right-in-list=<name>` | Right input reads an `IN`-list index probe |

### Runtime-only suffixes (EXPLAIN (ANALYZE))

| Suffix | Meaning |
| --- | --- |
| `ran=grace-hash` | Hash join spilled and ran as a Grace hash join |
| `ran=nested-loop-fallback` | Hash build exceeded its limit (`HashJoinMaxBuildRows`, default 1,000,000) with spill off |
| `ran=nested-loop-rescan` | Nested-loop join reread its right side for every left row |
| `ran=broadcast` | Distributed hash join broadcast the build side. One `broadcast-span` row follows per span |
| `probe-memo-hits=<n>` | Index nested-loop join answered `n` probes from its memo. Shown only above zero |
| `probe-batches=<n>` | Index nested-loop join probed in `n` pages of outer rows. Shown only above zero |
| `join-order-est-rows=<n>` | Row estimate from the join-order search. Shown only when the search costed the order |
| `partitions=<n> rows=<n> spilled=<n>` | On `window`: partitions computed, rows they held, partition buffers moved to disk |

### The lock step under EXPLAIN (ANALYZE)

The `lock_rows` detail adds three counters. They print also when they are zero.

| Counter | Meaning |
| --- | --- |
| `locked=<n>` | Row locks the step got, including rows that `recheck-dropped` counts |
| `skipped-held=<n>` | Rows that `SKIP LOCKED` left out because another transaction held them |
| `recheck-dropped=<n>` | Rows locked and not returned: a concurrent commit deleted them or moved them out of `WHERE` |

`actual_rows` of the step is `locked - recheck-dropped`. A dropped row stays locked
until the transaction ends.

### `plan-info` suffixes under EXPLAIN (ANALYZE)

| Suffix | Meaning |
| --- | --- |
| `planning-ms=<ms>` | Time of the bind and plan stages, including subqueries folded during bind |
| `join-order=cost-based` | The cost-based search chose the order |
| `join-order=greedy` | The search passed its step budget. A greedy order was built instead |
| `join-order=heuristic` | The search is off, statistics are not loaded, the join has more than 30 tables, or no order avoids a cross product |
| `join-order=cached` | A plan-cache hit replayed an earlier order |
| `join-steps-costed=<n>` | Join steps the search costed. Same count per run for the same query and statistics, so compare it between versions |

## Informational rows

They follow the operator rows. Their estimate columns are `NULL`.

| Node | When | Detail |
| --- | --- | --- |
| `plan-info` | Plan carries a shape id | `shape=<id>, schema-deps=[table@version, ...]` |
| `cache` | Query has a `{cache=...}` hint | `family=<name>, eligible=<bool>[, reason=<why>], ttl=<n>ms\|default, strict=<bool>` |
| `distribution` | Distributed execution enabled | `distributed=yes` or `distributed=no (<reason>)` |
| `gather-span` | `EXPLAIN (ANALYZE)` of a distributed scan | `partition <n>: mode=local\|remote\|remote-fallback, ...` |
| `broadcast-span` | `EXPLAIN (ANALYZE)` of a broadcast join | Same columns as `gather-span` |

`cache` reasons: `join` (joins are never cached) and `cache-disabled`. The row
is static. It does not probe the cache. The runtime `cacheStatus` in the
response is authoritative.

## Join plans

- A join row shows only its left input as the next row. The right input has no
  row of its own.
- The join row's storage counters describe the work on its right side.
- `nested-loop-join` reads a base-table right side once and keeps its rows, so
  `rows_read` is the right rows one time. It reads nothing when the left side is
  empty.
- `nested-loop-join` rereads the right side for each left row in two cases: the
  right-side filter calls a volatile function, or spill is off and the right side
  exceeds `HashJoinMaxBuildRows`. Then `rows_read` is left rows × right rows, and the
  detail shows `ran=nested-loop-rescan`.
- `index-nested-loop-join` probes a page of outer rows at a time. The distinct keys
  of a page cost one batched index read and one batched row read.
- Unique probes add one `kv_lookups` per distinct non-NULL key. Non-unique probes add
  one `kv_scan_entries` per index entry. A probe stops at its key, so it visits no
  entry past the key.
- A repeated key is answered from the join's memo and adds nothing to the counters.
  `rows_read` counts the right rows fetched, before the right-side filter.
- A `function-scan` reads nothing from storage. Its `actual_rows` counts generated
  rows.
- `hash-join` counts one read of the right side.
- `merge-join` adds the reads of its ordered right input.
- The root of a join plan is the last join. Grouping, sorting, final filters,
  projection and `LIMIT` after the last join have no separate row.
- Hash and merge joins render only equality key pairs. Other `ON` conditions
  still execute.
- A condition can appear at an earlier join than written. The planner attaches
  each condition where its last referenced table enters.
- `nested-loop-join` with `on=true` is a Cartesian product step.
- `build=` names the hash table side. Inner joins build the smaller estimated
  side. Left outer joins always build the right side.

## Filter rows under ANALYZE

The filter is evaluated inside the scan loop:

- The `filter` row's `actual_rows` is the count that passed.
- Its `kv_lookups` and `kv_scan_entries` are `0`. Storage cost belongs to the
  scan row below.
- Its `actual_time_ms` is `NULL` unless it is the root.

## Estimates

`estimated_rows` and `estimated_cost` come from `CostEstimator.AnnotatePlan`.
Inputs: row count, min/max per column, histograms and distinct counts after
`ANALYZE`. Without statistics, estimates use fixed defaults. Use
`SHOW STATISTICS FOR <table>` to see the inputs. Read `estimate_trace` to see which
rule produced the number.

The join-order search has its own estimate. `join-order-est-rows` shows it next to
`estimated_rows` and `actual_rows`, so you can see which was closer. The plan
annotation estimates one row per outer row for an `index-nested-loop-join`.

Cost weights: KV point lookups, range entries and primary-row fetches cost 1.0
each. In-memory rows cost 0.1 each. `NetworkFactor` is added under key-range
sharding and is `0` on one node. A non-covering index scan pays twice per match
(entry + row fetch). The planner replaces a predicate range scan with a full scan
when estimated entries reach the 40 % breakeven.

## Diagnostic patterns

| Pattern | Likely cause | Fix |
| --- | --- | --- |
| `table-scan` with a selective `WHERE` | No usable index, or the leading index column is not filtered | Index the equality columns first |
| `filter` above `index-range-scan`, high `rows_read` | Index covers only part of the predicate | Add the filter column to the index key |
| `sort` under `limit` | No index for the order, or a bound condition failed | Index matching the `ORDER BY` columns and direction |
| `topk` with huge `rows_read` | Bounded sort, but every candidate is read | Index that supplies the order, or a tighter `WHERE` |
| `index-range-scan` without `index-only=true` | Query needs columns outside the index | `INCLUDE` the missing columns |
| `distinct hash` | Columns not `NOT NULL`, or not in index order | Index the distinct columns, make them `NOT NULL` |
| `nested-loop-join` | No equality key or no right-side index | Add an equality `ON` and index the right join key |
| `ran=nested-loop-rescan` | Volatile right-side filter, or a large right side with spill off | Remove the volatile call, enable spill, or index the right join key |
| `index-nested-loop-join`, high `rows_read`, few `probe-memo-hits` | Each key fans out to many right rows | Filter the right side |
| `join-order=greedy` or `heuristic` in `plan-info` | The search was over budget, off, or had no statistics | `ANALYZE` the tables, or reduce the joined tables |
| `window` with `spilled` above 0 | A partition passed the spill threshold | Narrow the `WHERE` |
| `filter` above `gin-scan`, high `rows_read` | The GIN candidates are a superset of the matches | Expected. Add a more selective JSONB conjunct |
| `ran=grace-hash` | Hash build side larger than its limit | Filter the build side, or index for a nested-loop or merge join |
| `computed_once=` reader under a `LIMIT`, high `rows_read` | The shared CTE runs in full before any reader gets a row | `NOT MATERIALIZED` when readers need only part of the result |
| Join marked `lateral`, slow | The lateral body runs for each left row | Filter the left side first |
| A `recursive-union` query fails with `CADB0560` | The recursion passed its step or row limit | Add a stopping predicate |
| Estimates off by orders of magnitude | Missing or stale statistics | `ANALYZE TABLE` |

## Examples

Full scan:

```text
stage     node        detail
physical  table-scan  table=robots
```

Range scan with a residual filter:

```text
physical  filter            name = 'Bishop'
physical  index-range-scan  index=year_idx, from>=2020
```

Sort elided by an index:

```text
physical  index-range-scan  index=year_idx
```

`LIMIT` pushed into a scan:

```text
physical  limit       10
physical  table-scan  table=robots
```

Bounded sort:

```text
physical  topk  k: 10, l2_distance(…) ASC
```

Set operation:

```text
union        distinct: hash
operand      left
project
filter       x > 1
table-scan   table=a
operand      right
project
table-scan   table=b
```

Locking read under `EXPLAIN (ANALYZE)`, with rows 2 and 3 held by another
transaction:

```text
node       detail                                                                actual_rows
limit      3                                                                     3
lock_rows  for update, skip locked, locked=3, skipped-held=2, recheck-dropped=0  3
```

`EXPLAIN (ANALYZE)` of an indexed join:

```text
stage    node                    detail                                   actual_rows  rows_read  kv_lookups  kv_scan_entries
analyze  index-nested-loop-join  on=o.customer_id=c.id, index=orders_idx  6            6          0           10
analyze  table-scan              table=customers                          4            4          0           4
```

Sources: https://camusdb.github.io/docs/explain,
https://camusdb.github.io/docs/query-planner-internals,
https://camusdb.github.io/docs/sql-queries (WITH queries),
https://camusdb.github.io/docs/joins-and-subqueries (LATERAL and VALUES sources),
https://camusdb.github.io/docs/spill-to-disk
