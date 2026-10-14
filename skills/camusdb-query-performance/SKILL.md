---
name: camusdb-query-performance
description: Diagnoses and fixes slow CamusDB SQL queries. Guides the agent through SHOW SLOW QUERIES and SHOW QUERIES, EXPLAIN and EXPLAIN (ANALYZE) plan reading (table-scan, index-range-scan, index-only=true, topk, hash-join, merge-join, semi-join, gather), SHOW STATISTICS FOR, ANALYZE TABLE and automatic analyze, CREATE INDEX with INCLUDE covering indexes, @{FORCE_INDEX=...}, {cache=..., ttl=..., strict} result-cache hints and EVICT CACHE, AS OF SYSTEM TIME historical reads, spill to disk, distributed query execution, SHOW RANGES and key-range sharding, and SQL routing advice. Use when a CamusDB query is slow, a plan looks wrong, estimates differ from actual rows, a query does a full scan or spills, or errors CADB0549, CADB0536 or CADB0507 appear.
---

# CamusDB query performance

This skill makes a slow CamusDB query fast. CamusDB has a rule-based planner
with cost-based passes on top. You read its choice with `EXPLAIN`, measure it
with `EXPLAIN (ANALYZE)`, and steer it with indexes, statistics, rewrites and
hints. Change one thing at a time and measure again.

## Workflow

Copy this checklist and work through it.

```text
- [ ] 1. Capture the exact SQL, its parameters and the table sizes
- [ ] 2. Run plain EXPLAIN and name the access path of every table
- [ ] 3. Run EXPLAIN (ANALYZE) and compare estimates with actuals
- [ ] 4. Check SHOW STATISTICS FOR each table; ANALYZE if stale or missing
- [ ] 5. Pick one fix: index, covering index, rewrite, cache hint, historical read
- [ ] 6. Re-run EXPLAIN (ANALYZE) and compare counters with step 3
```

### 1. Capture the query

```sql
SHOW QUERIES LIKE '%FROM orders%';   -- running now, with elapsed_ms and rows_returned
SHOW SLOW QUERIES LIKE '%orders%';   -- recent slow statements on this node
```

- The slow query log is off by default. Enable it in `config.yml` with
  `slow_query_log_enabled: true` and `slow_query_log_threshold_ms: 1000`.
- `SHOW SLOW QUERIES` needs a superuser when authentication is on. It is
  node-local and in memory only.
- Read `full_scan`, `spilled`, `rows_read` and `rows_returned`. Many rows read
  for few returned means a missing or unused index.
- Stop a runaway read with `CANCEL QUERY '<query_id>'`. Only reads are
  cancellable.

### 2. Read the plan

```sql
EXPLAIN SELECT id, total FROM orders WHERE customer_id = 42 ORDER BY total DESC LIMIT 10;
```

Plain `EXPLAIN` builds the plan and reads no rows. One exception: an
uncorrelated subquery runs once during planning. Rows come parent first; read
from the bottom row upward. Read
[references/explain-nodes.md](references/explain-nodes.md) for every node
name, detail field and informational row.

Answer these questions:

- Is the leaf `table-scan`, `index-lookup`, `index-range-scan` or
  `index-in-list`?
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
`kv_scan_entries`.

| Signal | Meaning |
| --- | --- |
| `rows_read` ≫ `actual_rows` on a scan | The filter rejects most rows. Index the filter columns. |
| `estimated_rows` far from `actual_rows` | Statistics are missing or stale. Go to step 4. |
| `rows_read = 0` on an index scan | Covered read. No primary-row fetch. |
| High `kv_scan_entries` on `table-scan` | Full scan. Look for a usable index. |
| `nested-loop-join` with large `rows_read` | Right side reread per left row. Index the right join key. |
| `ran=grace-hash` on a join | Hash join spilled to disk. |
| `ran=nested-loop-fallback` on a join | Hash build exceeded its limit with spill off. |

### 4. Check statistics

```sql
SHOW STATISTICS FOR orders;
ANALYZE TABLE orders;
```

- `histogram_buckets` or `distinct_count` is `NULL`: no `ANALYZE` ran. The
  planner uses fixed fallback selectivity.
- `last_analyzed` is `NULL`: never analyzed. The cost-based access-path pass
  stays off for this table.
- `stale_mutations` near `estimated_rows`: distributions are old.
- Run `ANALYZE TABLE` after a bulk load, a `TRUNCATE` and refill, or a big
  change in data distribution.
- Automatic analyze is on by default. It re-analyzes when mutations reach
  `0.20 × row_count + 500`. It sweeps every 60 s.
- Statistics are cached per node. Run `SHOW STATISTICS` on the node that ran
  `ANALYZE`.
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

**Rewrite.** Turn `IN (SELECT ...)` and `NOT IN (SELECT ...)` into index-backed
semi-joins and anti-joins by indexing the inner column. Index the correlated
columns of an `EXISTS`. Use qualified names in joins.

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

**Test an index choice.** Force it, measure, then remove the hint:

```sql
SELECT id, name FROM robots@{FORCE_INDEX=robots_year_idx} WHERE year >= 1980;
```

CamusDB honors the hint even when it makes the query slower.

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
- **Pattern predicates never drive an index.** `LIKE`, `ILIKE`, `~`, `~*`,
  `!~`, `!~*` are residual filters.
- **A predicate on an `INCLUDE` column is a filter**, not an index bound.
- **Composite indexes work left to right.** Skipping the leading column can
  make the index unusable.
- **Cost-based passes need statistics.** `cost_based_access_path_enabled`
  needs an analyzed table. `cost_based_join_order_enabled` needs loaded row
  counts and handles up to 12 sources.
- **`UPDATE` and `DELETE` scans use the heuristic path.** This keeps lock ranges
  small.
- **The plan cache is off by default** (`plan_cache_enabled: false`). When on,
  it reuses decisions across literal values.

## Spill to disk

Sorts, hash joins, `GROUP BY`, hash `DISTINCT`, derived tables and DML match
sets can spill. Spill is off by default (`spill_enabled: false`). The threshold
is `spill_threshold_rows: 500000` per operator.

| Error | Meaning | Action |
| --- | --- | --- |
| `CADB0549` SpillLimitExceeded | Node exceeded `spill_max_total_bytes` (8 GiB default) | Add `LIMIT`, an index for the order, or a filter |
| `CADB0536` InsufficientDiskSpace | Spill would cross `min_free_disk_bytes` | Free disk space |
| `CADB0507` SpillStorageUnavailable | Spill file could not be created | Fix `data_dir` permissions or space |

A spilled query trades memory for disk I/O. An index that supplies the order
removes the sort entirely.

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
- Running `EXPLAIN` on `UPDATE` or `DELETE`. `EXPLAIN` accepts a `SELECT` only.
  Explain the equivalent `SELECT ... WHERE`.
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
- Expecting `pg_stat_statements`. Use `SHOW SLOW QUERIES` and `SHOW QUERIES`.
- Writing `COUNT(DISTINCT x)`, `FULL OUTER JOIN`, `NATURAL JOIN` or
  `JOIN ... USING`. These are not supported. Rewrite the query.
- Expecting `NOT IN (1, 2, 3)` to probe an index. A `NOT IN` value list stays a
  filter.
- Expecting fresh statistics to replan a cached plan immediately. Check whether
  the plan cache is on.
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
  - https://camusdb.github.io/docs/best-practices
  - https://camusdb.github.io/docs/time-travel-reads
