---
name: camusdb-query-performance
description: Diagnoses and fixes slow CamusDB SQL queries. Guides the agent through SHOW STATEMENT STATS, SHOW SLOW QUERIES, SHOW QUERIES and SHOW INDEX USAGE, EXPLAIN and EXPLAIN (ANALYZE) plan reading (table-scan, index-range-scan, index-only=true, gin-scan, topk, window, union, lock_rows, hash-join, merge-join, index-nested-loop-join, estimate_trace, gather), SHOW STATISTICS FOR, ANALYZE TABLE and automatic analyze, CREATE INDEX with INCLUDE covering indexes, DROP INDEX, @{FORCE_INDEX=...}, {cache=..., ttl=..., strict} result-cache hints and EVICT CACHE, AS OF SYSTEM TIME historical reads, spill to disk, buffered result limits, distributed query execution, SHOW RANGES and key-range sharding, and SQL routing advice. Use when a CamusDB query is slow, a plan looks wrong, estimates differ from actual rows, a query does a full scan or spills, an index looks unused, or errors CADB0549, CADB0536, CADB0507 or CADB0556 appear.
---

# CamusDB query performance

This skill makes a slow CamusDB query fast. CamusDB has a rule-based planner
with cost-based passes on top. You read its choice with `EXPLAIN`, measure it
with `EXPLAIN (ANALYZE)`, and steer it with indexes, statistics, rewrites and
hints. Change one thing at a time and measure again.

## Workflow

Copy this checklist and work through it.

```text
- [ ] 1. Find the query; capture its exact SQL, parameters and table sizes
- [ ] 2. Run plain EXPLAIN and name the access path of every table
- [ ] 3. Run EXPLAIN (ANALYZE) and compare estimates with actuals
- [ ] 4. Check SHOW STATISTICS FOR each table; ANALYZE if stale or missing
- [ ] 5. Pick one fix: index, covering index, drop an index, rewrite, cache hint, historical read
- [ ] 6. Re-run EXPLAIN (ANALYZE) and compare counters with step 3
```

### 1. Capture the query

```sql
SHOW STATEMENT STATS LIKE '%orders%';   -- running totals per statement shape
SHOW QUERIES LIKE '%FROM orders%';      -- running now, with elapsed_ms and rows_returned
SHOW SLOW QUERIES LIKE '%orders%';      -- recent slow statements on this node
```

Start with `SHOW STATEMENT STATS` when you do not know which query to tune. It
finds a statement that is fast each time but runs often. The slow query log
never sees that statement.

| Column | Look for |
| --- | --- |
| `total_ms` | The statements that use the most node time |
| `rows_read`, `rows_returned` | Many rows read for few returned: a missing or unused index |
| `full_scans`, `spills` | Executions that read a whole relation or wrote a spill file |
| `plan_changes`, `last_plan` | A plan that changed between executions |
| `cv` | High means two regimes, such as a plan that changes. Rows sort by `cv`, highest first |

- Statement statistics are off by default. Run
  `SET CLUSTER SETTING statement_stats_enabled = true`. The next statement counts.
- The totals are cumulative. Read twice and subtract to measure a window.
- `sample_sql` is the first text seen, not the slowest.
- A moving `statement_stats.dropped` counter in `SHOW ENGINE STATS` means the table
  is full. Raise `statement_stats_max_shapes` (needs a restart).
- The slow query log is off by default. Enable it in `config.yml` with
  `slow_query_log_enabled: true` and `slow_query_log_threshold_ms: 1000`, or run
  `SET CLUSTER SETTING slow_query_log_enabled = true`.
- `SHOW STATEMENT STATS` and `SHOW SLOW QUERIES` need a superuser when
  authentication is on. Both are node-local and in memory only.
- In a slow-log row, read `full_scan`, `spilled`, `rows_read` and `rows_returned`.
- `SHOW CLUSTER QUERIES` lists every node. Past
  `cluster_activity_max_concurrent_fan_outs` it fails with `CADB0558`. Retry it.
- Stop a runaway read with `CANCEL QUERY '<query_id>'`. Only reads are
  cancellable. CamusDB has no statement timeout.

### 2. Read the plan

```sql
EXPLAIN SELECT id, total FROM orders WHERE customer_id = 42 ORDER BY total DESC LIMIT 10;
```

Plain `EXPLAIN` builds the plan and does not run the query. Two exceptions read
storage: an uncorrelated subquery runs once during planning, and the join planner
can read a small filtered dimension table (see
[references/planner-rules.md](references/planner-rules.md)). Rows come parent
first; read from the bottom row upward. Read
[references/explain-nodes.md](references/explain-nodes.md) for every node
name, detail field and informational row.

Answer these questions:

- Is the leaf `table-scan`, `index-lookup`, `index-range-scan`,
  `index-in-list` or `gin-scan`?
- Is there a `filter` above the scan? Those predicates are not index bounds.
- Is there a `sort`? Then no index supplies the order.
- Is there a `topk` instead? Then a `LIMIT` bounds the sort.
- Does the scan show `index-only=true`? Then the index covers the query.
- Which join algorithm runs, and which side is `build=`?

### 3. Measure

```sql
EXPLAIN (ANALYZE) SELECT id, total FROM orders WHERE customer_id = 42 LIMIT 10;
```

`EXPLAIN (ANALYZE)` executes the full query and drains it. Extra columns:
`actual_rows`, `rows_read`, `actual_time_ms` (root row only), `kv_lookups`,
`kv_scan_entries`, `estimate_trace`.

**Warning: `EXPLAIN (ANALYZE)` of `SELECT ... FOR UPDATE` takes real row locks.**
The rows stay locked until the transaction ends, and `NOWAIT` can fail it with
`CADB0555`. Roll back after you measure. Plain `EXPLAIN` of the same statement
takes no lock and shows a `lock_rows` row. Under `ANALYZE` that row adds
`locked=`, `skipped-held=` and `recheck-dropped=`.

| Signal | Meaning |
| --- | --- |
| `rows_read` ≫ `actual_rows` on a scan | The filter rejects most rows. Index the filter columns. |
| `estimated_rows` far from `actual_rows` | Statistics are missing or stale. Read `estimate_trace` for the rule and inputs, then go to step 4. |
| `rows_read = 0` on an index scan | Covered read. No primary-row fetch. |
| High `kv_scan_entries` on `table-scan` | Full scan. Look for a usable index. |
| `ran=nested-loop-rescan` on a join | Right side reread for each left row. Index the right join key. |
| `ran=grace-hash` on a join | Hash join spilled to disk. |
| `ran=nested-loop-fallback` on a join | Hash build exceeded its limit with spill off. |

### 4. Check statistics

```sql
SHOW STATISTICS FOR orders;
ANALYZE TABLE orders;
```

- `histogram_buckets` or `distinct_count` is `NULL`: `ANALYZE` has not collected
  that column. The planner uses fixed fallback selectivity.
- `last_analyzed` is `NULL`: never analyzed. The cost-based access-path pass
  stays off for this table.
- `stale_mutations` near `estimated_rows`: distributions are old.
- Run `ANALYZE TABLE` after a bulk load, a `TRUNCATE` and refill, or a big
  change in data distribution.
- Automatic analyze is on by default. It re-analyzes when mutations reach
  `0.20 × row_count + 500`. It sweeps every 60 s.
- Statistics are cached per node. Run `SHOW STATISTICS` on the node that ran
  `ANALYZE`. Another node plans with its older copy until it reloads them.
- Estimates still wrong with fresh statistics? The cost model is the limit.
  Change the query or the index, not the statistics.

### 5. Fix

Pick the smallest fix that matches the diagnosis.

**Index the filter, join and sort columns.** Equality columns go first, then
one range column:

```sql
CREATE INDEX orders_customer_created_idx ON orders (customer_id, created_at DESC);
```

**Cover a hot read.** `INCLUDE` adds payload columns. The scan then fetches no
primary row:

```sql
CREATE INDEX orders_customer_idx ON orders (customer_id) INCLUDE (status, total);
SELECT customer_id, status, total FROM orders WHERE customer_id = 42;
```

**Match the sort direction.** CamusDB scans an index forward only.
`ORDER BY created_at DESC` needs an index declared `created_at DESC`.

**Bound the result.** Add `LIMIT`. An ordered index scan can then stop early.
Without such an index, `topk` still bounds memory but reads every candidate.

**Drop an unused index.** An index that no statement reads still costs writes:

```sql
SHOW INDEX USAGE FROM orders;
DROP INDEX orders_legacy_ref_idx ON orders;
```

- Drop only when `reads` and `constraint_reads` are 0 and `writes` is not 0.
- Compare the window with `tracked_since`. Zero reads over one hour proves nothing.
- Check every node. The counts are per node and reset on restart.
- Do not drop a unique index or a primary key because `reads` is 0. It enforces
  a constraint.
- `SHOW INDEX USAGE` needs a superuser. Plain `EXPLAIN` adds no count.

**Rewrite.** Turn `IN (SELECT ...)` and `NOT IN (SELECT ...)` into index-backed
semi-joins and anti-joins by indexing the inner column. Index the correlated
columns of an `EXISTS`. Use qualified names in joins.

- An uncorrelated subquery runs once and folds to its value, in every expression
  position and at any depth.
- A correlated `EXISTS` works only in the `WHERE` of a `SELECT`. It runs per row.
  Elsewhere it fails with `CADB0533`.
- A correlated `IN` or `NOT IN` is refused. Rewrite it as a correlated `EXISTS`.

**Cache a repeated single-table read.**

```sql
SELECT id, total FROM orders {cache=recent_orders, ttl=30s} WHERE status = 'paid';
```

Read [references/result-cache.md](references/result-cache.md) before you add a
hint. It works only in autocommit mode, for one table, per node.

**Read the past for reports.** A historical read blocks no writer:

```sql
SELECT player_id, score FROM leaderboard AS OF SYSTEM TIME '-10s'
ORDER BY score DESC LIMIT 100;
```

**Index a JSONB filter.** Use a GIN index for `@>`, `?`, `?|`, `?&` and
`->> 'k' = text`. Other forms and joins do not use it:

```sql
CREATE INDEX events_payload_gin ON events USING GIN (payload);
```

**Test an index choice.** Force it, measure, then remove the hint:

```sql
SELECT id, name FROM robots@{FORCE_INDEX=robots_year_idx} WHERE year >= 1980;
```

When the predicate can bound the named index, that bounded scan is the plan, even
when it is slower. When it cannot, the automatic choice runs. The hint becomes an
end-to-end scan of the index only if no automatic scan is chosen. A name that is
not a readable index of the table is an error.

Read [references/planner-rules.md](references/planner-rules.md) when you need
to predict which index, sort or join algorithm the planner picks.

### 6. Confirm

Re-run the same `EXPLAIN (ANALYZE)`. Compare `rows_read`, `kv_scan_entries`,
`kv_lookups` and root `actual_time_ms` with step 3. Keep the fix only when the
counters drop. Repeat with production-like data and statistics.

## Key rules

- **Unique equality is a point lookup.** `index-lookup` on `~pk` or a `UNIQUE`
  index reads one row.
- **Non-unique equality is a range scan.** It shows `index-range-scan`.
- **Any index column type can serve an equality.** A value with no successor
  still seeks the index. The cost model can still pick a full scan.
- **Pattern predicates never drive an index.** `LIKE`, `ILIKE`, `~`, `~*`,
  `!~`, `!~*` are residual filters. `LIKE` and `ILIKE` are still priced from the
  histogram.
- **A predicate on an `INCLUDE` column is a filter**, not an index bound.
- **Composite indexes work left to right.** Skipping the leading column can
  make the index unusable.
- **Cost-based passes need statistics.** `cost_based_access_path_enabled`
  needs an analyzed table. `cost_based_join_order_enabled` needs loaded row
  counts and handles up to 30 tables. Past 2,000,000 costed steps it takes a
  greedy order.
- **`UPDATE` and `DELETE` scans use the heuristic path.** This keeps lock ranges
  small.
- **The plan cache is off by default** (`plan_cache_enabled: false`). When on,
  it reuses decisions across literal values. A fresh `ANALYZE` makes the next
  lookup on that node plan again.

## Spill to disk

Sorts, hash joins, `GROUP BY`, hash `DISTINCT`, `UNION`, `INTERSECT`, `EXCEPT`,
window functions, derived tables and DML match sets can spill. `UNION ALL` never
does. Spill is off by default (`spill_enabled: false`). The per-operator thresholds
are `spill_threshold_rows: 500000` and `spill_threshold_bytes: 268435456`.

| Error | Meaning | Action |
| --- | --- | --- |
| `CADB0549` SpillLimitExceeded | Node exceeded `spill_max_total_bytes` (8 GiB default) | Add `LIMIT`, an index for the order, or a filter |
| `CADB0536` InsufficientDiskSpace | Spill would cross `min_free_disk_bytes` | Free disk space |
| `CADB0507` SpillStorageUnavailable | Spill file could not be created | Fix `data_dir` permissions or space |

A spilled query trades memory for disk I/O. An index that supplies the order
removes the sort entirely.

## Result and statement limits

| Error | Limit | Action |
| --- | --- | --- |
| `CADB0556` ResultSetTooLarge | `max_buffered_result_rows` (1,000,000) or `max_buffered_result_bytes` (256 MiB) on one buffered response | Use a streaming transport, or page with `LIMIT` and `OFFSET` |
| `CADB0557` StatementTooLarge | `max_statement_bytes` (8 MiB) of inline SQL text | Bind the values as parameters |
| `CADB0558` ConcurrencyLimitExceeded | `max_open_explicit_transactions` (10,000) or the cluster fan-out cap | Retry after a back-off |

- `CADB0556` applies to `/execute-sql-query` and `/query`. It rolls back the
  statement's transaction, including a write with `RETURNING`.
- Streaming transports are not capped: `/execute-sql-query-stream` and the gRPC
  query stream.
- All are runtime settings. A value of `0` or less disables a limit.

## Clusters

Distributed execution is off by default. It needs `key_range_sharding: true`
and `distributed_query_execution: true`. Only full scans of primary rows split
into fragments. Read
[references/distributed-execution.md](references/distributed-execution.md) for
`gather`, `gather-span`, broadcast joins, `SHOW RANGES`, parallel scans and
routing advice.

## Common mistakes

- Writing `EXPLAIN ANALYZE SELECT ...` as in PostgreSQL. CamusDB needs the
  parentheses: `EXPLAIN (ANALYZE) SELECT ...`.
- Adding `VERBOSE`, `BUFFERS` or `FORMAT JSON`. CamusDB rejects unknown
  options. Valid options are `LOGICAL`, `PHYSICAL`, `ANALYZE`.
- Running `EXPLAIN` on `UPDATE` or `DELETE`. `EXPLAIN` accepts a `SELECT` only,
  including a set operation and `FOR UPDATE`. Explain the equivalent
  `SELECT ... WHERE`.
- Using CockroachDB `STORING (...)`. CamusDB uses `INCLUDE (...)`.
- Using CockroachDB `table@index` or MySQL `USE INDEX`. Write
  `table@{FORCE_INDEX=index}`.
- Expecting a backward scan of an ascending index. Create the index with the
  direction the `ORDER BY` uses.
- Declaring a `DESC` key on a `STRING` or `BYTES` column. CamusDB rejects it with
  `InvalidInput`.
- Expecting `LIKE 'abc%'` to use a B-tree index. It is always a residual filter.
- Expecting a hash-sharded index. None exists. Lead with a well-spread column.
- Trusting `estimated_cost` as milliseconds. It is unitless model work.
- Treating a missing `ON` condition in `EXPLAIN` as skipped. Hash and merge joins
  show only key pairs. A final post-join filter has no row.
- Adding a cache hint to a join or inside `BEGIN ... COMMIT`. It has no effect.
- Expecting `pg_stat_statements`. Use `SHOW STATEMENT STATS`.
- Expecting `pg_stat_user_indexes`. Use `SHOW INDEX USAGE`.
- Putting a correlated `EXISTS` in the select list, `HAVING` or `JOIN ON`. It fails
  with `CADB0533`. A subquery in `ORDER BY` or `GROUP BY` fails with `CADB0400`.
- Writing `COUNT(DISTINCT x)`, `FULL OUTER JOIN`, `NATURAL JOIN` or
  `JOIN ... USING`. These are not supported. Rewrite the query.
- Expecting `NOT IN (1, 2, 3)` to probe an index. A `NOT IN` value list stays a
  filter.
- Expecting `ANALYZE` on one node to change plans on another node. Another node
  keeps its loaded statistics until the entry is evicted or the node restarts.
- Expecting a `LIMIT` to cut the rows a window function reads. Filter in `WHERE`.
- Parameterizing a filter you want pushed to remote fragments. A parameter
  placeholder is not shippable, so the filter runs on the coordinator.

## References

- [references/explain-nodes.md](references/explain-nodes.md): read when
  interpreting any `EXPLAIN` or `EXPLAIN (ANALYZE)` row.
- [references/planner-rules.md](references/planner-rules.md): read when
  predicting index choice, sort elision, join algorithm, join order or subquery
  rewrites.
- [references/result-cache.md](references/result-cache.md): read before adding
  `{cache=...}` or using `EVICT CACHE`.
- [references/distributed-execution.md](references/distributed-execution.md):
  read for clusters, key-range sharding, `SHOW RANGES` and routing advice.
- Public docs:
  - https://camusdb.github.io/docs/explain
  - https://camusdb.github.io/docs/query-planning
  - https://camusdb.github.io/docs/query-planner-internals
  - https://camusdb.github.io/docs/show-statistics
  - https://camusdb.github.io/docs/automatic-analyze
  - https://camusdb.github.io/docs/sql-indexes
  - https://camusdb.github.io/docs/query-result-cache
  - https://camusdb.github.io/docs/spill-to-disk
  - https://camusdb.github.io/docs/query-activity
  - https://camusdb.github.io/docs/slow-query-log
  - https://camusdb.github.io/docs/statement-stats
  - https://camusdb.github.io/docs/index-usage
  - https://camusdb.github.io/docs/json
  - https://camusdb.github.io/docs/best-practices
  - https://camusdb.github.io/docs/time-travel-reads
