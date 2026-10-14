# CamusDB planner rules

Read this file to predict which access path, sort strategy, join algorithm and
join order the planner picks. Then design the index or rewrite that gets the
plan you want.

## Planner configuration

| Setting | Default | Effect |
| --- | --- | --- |
| `cost_based_access_path_enabled` | `true` | Compares index candidates and a full scan by estimated cost |
| `cost_based_join_order_enabled` | `true` | Searches left-deep inner-join orders by cost |
| `plan_cache_enabled` | `false` | Reuses planning decisions across literal values |
| `plan_cache_max_entries` | `512` | Plan cache capacity |
| `max_query_parallelism` | `1` | Decode workers per full scan, per node |
| `spill_enabled` | `false` | Lets blocking operators spill to disk |
| `spill_threshold_rows` | `500000` | Rows one operator holds before spilling |

Turning a cost-based flag off keeps cost annotations and the heuristic
cross-product repair.

## Single-table access paths

The planner picks one of five scans:

1. Full table scan (`table-scan`).
2. Forced index scan (`table-scan ... forced-index=`), from
   `t@{FORCE_INDEX=idx}`.
3. Point lookup on a unique index (`index-lookup`).
4. Range scan on an index (`index-range-scan`).
5. `IN`-list probes (`index-in-list`), when repeated probes beat a wider scan.

### Heuristic scoring

- Full equality on a unique index beats every other candidate.
- Equality on a non-unique index, or an equality prefix, is a strong candidate.
- An equality prefix plus a range on the next column drives a composite range
  scan.
- An index whose prefix matches the `ORDER BY` can win without any filter.
- An `IN (...)` list on an indexed column competes with range and full scans.

### Cost-based selection

Runs when `cost_based_access_path_enabled` is on, the table has a positive
row-count estimate, and the table has analyzed statistics. A table never
analyzed keeps the heuristic choice. `UPDATE` and `DELETE` row-location scans
always use the heuristic path.

### Predicate handling

- The planner splits predicates into index-servable comparisons,
  column-to-column comparisons and residual conjuncts.
- Comparisons implied by the scan bounds are removed. The rest becomes the
  `filter`.
- Literals are normalized to the column type before index matching. Valid text
  literals convert for `OID` and `UUID` columns.
- A range bound of `NULL` cannot drive a seek.
- `LIKE`, `ILIKE`, `~`, `~*`, `!~`, `!~*` are always residual filters.
- A `NOT IN (...)` value list is always a filter.
- A unique composite index gives a point lookup only when the predicate fixes
  every key column needed for one row. Nullable trailing columns prevent it.

### Covering indexes

```sql
CREATE INDEX orders_customer_idx ON orders (customer_id) INCLUDE (status, total);
```

- A query is covered when every needed column is a key or `INCLUDE` column.
  That includes columns used only in filters, ordering or aggregation.
- A covered scan shows `index-only=true` and `rows_read = 0` under
  `EXPLAIN (ANALYZE)`.
- A predicate on an `INCLUDE` column is a filter, not a bound.
- Limits: 32 columns per index (key + included), `max_index_columns`. 4 KiB
  encoded `INCLUDE` payload per entry, `max_index_include_tuple_bytes`.
- A primary key does not support `INCLUDE`.
- To change an `INCLUDE` list, drop the index and create it again.
- `INCLUDE` may hold `BYTES` and `ARRAY(T)` columns. Key columns cannot be
  arrays.

Create covering indexes three ways:

```sql
CREATE INDEX orders_customer_idx ON orders (customer_id) INCLUDE (status, total);
ALTER TABLE orders ADD INDEX orders_customer_idx (customer_id) INCLUDE (status, total);
-- inside CREATE TABLE:
--   KEY orders_customer_idx (customer_id) INCLUDE (status, total)
```

### Composite index design

- Put equality columns first, then one range or order column.
- Put the most selective equality column first when queries follow that shape.
- The `ORDER BY` columns and directions must match an index prefix, left to
  right. `(kind ASC, year DESC)` serves `ORDER BY kind ASC, year DESC`.
- Under key-range sharding, do not lead an index with a time column. Every
  insert then hits the last range.

## Sort elision and ordering

- An index scan in the requested order removes the `sort` node.
- Index scans run forward only. `(year ASC)` serves `ORDER BY year ASC`, not
  `DESC`.
- `DESC` index columns are allowed for `OID`, `UUID`, `INT64`, `FLOAT64`,
  `FLOAT32`, `BOOL`, `DATE` and `DATETIME`. `STRING` and `BYTES` `DESC` columns
  are rejected with `InvalidInput`.

### Bounded sort (`topk`)

A sort feeding a `LIMIT` keeps only `offset + limit` rows. It still reads every
qualifying input row. The full `sort` stays when:

- `LIMIT` is unknown or negative, or `OFFSET` is negative.
- `offset + limit` overflows or exceeds the maximum 32-bit integer.
- Spill is on and the bound exceeds the spill threshold.
- An operator between sort and limit changes the row count, such as an
  aggregate.

### LIMIT pushdown

The scan stops early when all hold:

- The scan already supplies the requested order.
- No extra filter runs after the scan.
- No `GROUP BY`, `HAVING` or `DISTINCT` blocks an early stop.

### DISTINCT

- Streaming (`distinct streaming: true`): projected columns are simple column
  names, all `NOT NULL`, and the scan order covers them. Constant memory.
- Hash (`distinct hash`): every other case, including `SELECT DISTINCT *`.
  Can spill.

## Operator chains

| Query shape | Chain, leaf to root |
| --- | --- |
| Plain select | `Scan -> [Filter] -> [Sort] -> [Limit] -> [Aggregate] -> [Having] -> [Project]` |
| Grouped | `Scan -> [Filter] -> Aggregate -> [Having] -> [Sort] -> [Project] -> [Limit]` |
| Distinct | `Scan -> [Filter] -> [Aggregate] -> [Having] -> [Project] -> Distinct -> [Sort] -> [Limit]` |

## Joins

Supported: `JOIN`, `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN` (run as a swapped
left join), `CROSS JOIN`, comma joins. Not supported: `FULL [OUTER] JOIN`,
`NATURAL JOIN`, `JOIN ... USING (...)`, and a `RIGHT JOIN` whose left operand
is itself a join.

### Algorithms

| Algorithm | Chosen when |
| --- | --- |
| `index-nested-loop-join` | Right join key has an index and probes are cheaper |
| `hash-join` | Equality join where build and probe is cheaper, or no right index |
| `merge-join` | Both sides readable in join-key order, left estimate above 100 rows |
| `nested-loop-join` | Fallback, for example without an equality key |

Join step cost model (`L` = left rows, `R` = right rows after filter, `S` =
right table scan cost):

| Candidate | Cost |
| --- | --- |
| Index nested loop | `2 × L` |
| Hash join | `S + L + 0.1 × min(L, R)` |
| Nested loop | `max(1, L) × S` |

An available index lookup wins unless hash is strictly cheaper. The `2 × L`
model assumes one match per probe; high fan-out non-unique indexes cost more
than modeled. Merge join is not costed during order search.

- Hash join build side: smaller estimate for inner joins; always the right
  table for left outer joins.
- Hash build above `HashJoinMaxBuildRows` (default 1,000,000): Grace hash join
  with spill on; nested-loop fallback with spill off.
- Merge join buffers only the current run of equal keys.

### Join order

- Cost-based search: connected left-deep orders, dynamic programming, at most
  12 sources. Needs loaded row counts for all base tables.
- Never enumerates a cross product or bushy trees.
- Heuristic order scores each table: 0 for a unique-index equality, 1 for
  another indexed predicate, 2 for none. Lower scores go first.
- Cross-product repair works without statistics, up to 64 sources.
- An outer join anywhere in the tree keeps the written order.
- A volatile condition, such as one using `random()`, restricts movement.
- Join-key equality is estimated as `1 / max(NDV_left, NDV_right)`. Without
  counts the fallback selectivity is `0.1`.

### Join tuning

- Index the right-side join key, for example `posts(user_id)` for
  `JOIN posts p ON p.user_id = u.id`.
- Index both join keys of a large equi-join to make a merge join possible.
- Filter early. A filter on the preserved side of an outer join pushes down.
  A filter on the null-extended side stays above the join.
- Qualify column names in joins.

## Subqueries

| Shape | Plan |
| --- | --- |
| `x IN (SELECT key FROM t)`, indexed inner column | `semi-join` with index probes |
| `x NOT IN (SELECT key FROM t)`, indexed `NOT NULL` inner column | `anti-join` |
| Same, nullable inner column | `null-aware-anti-join` |
| Inner column without an index | Subquery materialized; no join node |
| Correlated `EXISTS` with an index on the correlated equality columns | Indexed seek per outer row |
| Correlated `EXISTS` without such an index | Full inner scan per outer row |
| Uncorrelated scalar subquery | Evaluated once, result used by the outer plan |
| Derived table in `FROM` | `derived-table-scan`, materialized once |

Example rewrite target:

```sql
CREATE INDEX posts_user_idx ON posts (user_id);

SELECT email
FROM app_users
WHERE EXISTS (
  SELECT * FROM posts
  WHERE posts.user_id = app_users.id AND posts.published = true
);
```

Under a Serializable transaction, the indexed `EXISTS` seek range is protected
against phantoms.

## Statistics

| Statistic | Maintained by |
| --- | --- |
| Row count, index entry counts, min/max of indexed columns | Every write (advisory, always approximately current) |
| Histograms, distinct counts per column and per composite key prefix | `ANALYZE` (manual or automatic) |

- Equality estimate: `1 / distinct_count`.
- Range estimate: from the histogram.
- `a = ? AND b = ?` over a composite index uses the measured `key` row for
  `a,b`, not a product of two selectivities.
- Manual `ANALYZE TABLE` counts exactly and runs in the caller's transaction.
  Inside an explicit read-write transaction, it can make that transaction
  abort.
- Automatic analyze samples (10,000-row reservoir, HyperLogLog for NDV), scans
  at most 50,000 rows/s, runs one table at a time, and backs off when
  foreground work in flight exceeds `auto_analyze_load_pause_threshold`
  (default 16).
- Stale rule: `mutations_since_analyze >= 0.20 × row_count + 500`.
- Opt one table out:

```sql
ALTER TABLE application_logs SET (sql_stats_automatic_collection_enabled = false);
```

- After `TRUNCATE`, previous statistics are ignored. Run `ANALYZE` after
  refilling.
- A materialized view gets counts at refresh but no histograms until analyzed.
- A plain view has no statistics (`CADB0523`).

## Plan cache

- Off by default. Caches decisions, not rows or literals.
- Single table: index name and scan kind. Join: alias order and build mode.
- Invalidated by schema changes, index changes and new analyzed statistics.
  DML row-count changes do not invalidate it.
- Risk: a decision made for one literal can be slow for a skewed literal.
  Disable it to compare against a fresh plan.
- Distributed (fragment) plans never come from the plan cache.

## Known planner limits

- Statistics are estimates. Skew and correlated columns mislead the model.
- `EXPLAIN (LOGICAL)` shows the physical tree.
- Descending use of an index is limited to indexes declared `DESC`.
- `COUNT(DISTINCT ...)` is not supported.
- A heuristic or repaired join input can get a fixed 10,000-row fallback
  estimate.

Sources: https://camusdb.github.io/docs/query-planning,
https://camusdb.github.io/docs/query-planner-internals,
https://camusdb.github.io/docs/sql-indexes,
https://camusdb.github.io/docs/show-statistics,
https://camusdb.github.io/docs/automatic-analyze,
https://camusdb.github.io/docs/spill-to-disk
