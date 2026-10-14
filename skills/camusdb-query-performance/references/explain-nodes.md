# Reading CamusDB EXPLAIN output

`EXPLAIN` output is diagnostic. Node names, column names and detail formats are
stable within one minor version. Do not parse it in production code.

## Forms

```sql
EXPLAIN SELECT ...              -- physical plan, no rows read
EXPLAIN (PHYSICAL) SELECT ...   -- identical to plain EXPLAIN
EXPLAIN (LOGICAL) SELECT ...    -- same tree, stage column = 'logical'
EXPLAIN (ANALYZE) SELECT ...    -- executes and drains the query, adds counters
```

- Only these three option words exist. Any other word is an error.
- `EXPLAIN` accepts a `SELECT` statement only.
- An uncorrelated subquery executes once during planning, even for plain
  `EXPLAIN`.
- A `SELECT` without `FROM` renders `constant-source`, `project`, and `limit`
  when present. `EXPLAIN (ANALYZE)` rejects it.

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

On a scan, `actual_rows <= rows_read`. The gap is rows rejected by the filter
folded into the scan.

## Row order

Rows come depth-first, parent before child. The first row is the root. Read
from the last row upward to follow data flow.

## Operator nodes

| Node | Appears for | Detail |
| --- | --- | --- |
| `table-scan` | Full table scan, or a forced index | `table=<name>`; forced adds `, forced-index=<name>` |
| `index-lookup` | Equality on a unique index (`~pk` or `UNIQUE`) | `index=<name>, key=<value>`; covered adds `, index-only=true` |
| `index-range-scan` | Range predicate, `BETWEEN`, equality on a non-unique index, or an ordered index read | `index=<name>, from>=<v>, to<<v>`; covered adds `index-only=true` |
| `index-in-list` | `col IN (v1, v2, ...)` on an indexed column | `index=<name>, values=<n>` (distinct values probed) |
| `filter` | Residual predicate not served by the index | `<expr>` |
| `aggregate` | `GROUP BY` or an aggregate function | `group=[...], aggs=[...]` |
| `having-filter` | `HAVING` | `<expr>` |
| `sort` | `ORDER BY` not satisfied by scan order, no usable bound | `<col> ASC/DESC, ...` |
| `topk` | `ORDER BY` bounded by `LIMIT`; keeps `offset + limit` rows; never spills | `k: <n>, <col> ASC/DESC, ...` |
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
| `derived-table-scan` | Subquery in `FROM` | `alias=<alias>` |
| `constant-source` | `SELECT` without `FROM` | `1 row` |
| `gather` | Distributed scan split by span | `spans: <n>, remote_leader_fraction: <f>` |

Range bound rendering: numeric types show a half-open range
(`from>=2023, to<2024`). `STRING` and `OID` equality shows
`from>=v, to<=v`. Both return the same rows.

### Extra detail suffixes on join rows

These suffixes are emitted by the engine renderer (`PlanRenderer.cs`). The
EXPLAIN doc mentions only some of them.

| Suffix | Meaning |
| --- | --- |
| `kind=left-outer` | `LEFT JOIN`, or a `RIGHT JOIN` swapped into a left join |
| `right-filter=<expr>` | Filter pushed to the right input of a nested-loop, index nested-loop or merge join |
| `build-filter=<expr>` | Filter pushed to the hash build side |
| `right-index=<name>` | Right input of a hash or nested-loop join reads an index range |
| `right-in-list=<name>` | Right input reads an `IN`-list index probe |

### Runtime-only suffixes (EXPLAIN (ANALYZE))

| Suffix | Meaning |
| --- | --- |
| `ran=grace-hash` | Hash join spilled and ran as a Grace hash join |
| `ran=nested-loop-fallback` | Hash build exceeded its limit (`HashJoinMaxBuildRows`, default 1,000,000) with spill off |
| `ran=broadcast` | Distributed hash join broadcast the build side |
| `join-order-est-rows=<n>` | Row estimate from the join-order search |

## Informational rows

They follow the operator rows. Their estimate columns are `NULL`.

| Node | When | Detail |
| --- | --- | --- |
| `plan-info` | Plan carries a shape id | `shape=<id>, schema-deps=[table@version, ...]` |
| `cache` | Query has a `{cache=...}` hint | `family=<name>, eligible=<bool>[, reason=<why>], ttl=<n>ms\|default, strict=<bool>` |
| `distribution` | Distributed execution enabled | `distributed=yes` or `distributed=no (<reason>)` |
| `gather-span` | `EXPLAIN (ANALYZE)` of a distributed scan | `partition <n>: mode=local\|remote\|remote-fallback, ...` |

`cache` reasons: `join` (joins are never cached) and `cache-disabled`. The row
is static. It does not probe the cache. The runtime `cacheStatus` in the
response is authoritative.

## Join plans

- A join row shows only its left input as the next row. The right input has no
  row of its own.
- The join row's storage counters describe the work on its right side.
- `nested-loop-join` counts every reread: 4 left rows × 7 right rows = 28
  `rows_read`.
- `index-nested-loop-join` counts probes. Unique probes add `kv_lookups`.
  Non-unique walks add `kv_scan_entries`.
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
`SHOW STATISTICS FOR <table>` to see the inputs.

Cost weights: KV point lookups, range entries and primary-row fetches cost 1.0
each. In-memory rows cost 0.1 each. `NetworkFactor` is added under key-range
sharding and is `0` on one node. A non-covering index scan pays twice per match
(entry + row fetch), so a range scan loses to a full scan near half the table.

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
| `ran=grace-hash` | Hash build side larger than its limit | Filter the build side, or index for a nested-loop or merge join |
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

`EXPLAIN (ANALYZE)` of an indexed join:

```text
stage    node                    detail                                   actual_rows  rows_read  kv_lookups  kv_scan_entries
analyze  index-nested-loop-join  on=o.customer_id=c.id, index=orders_idx  6            6          0           10
analyze  table-scan              table=customers                          4            4          0           4
```

Source: https://camusdb.github.io/docs/explain and
https://camusdb.github.io/docs/query-planner-internals
