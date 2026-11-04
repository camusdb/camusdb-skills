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
| `spill_threshold_bytes` | `268435456` | Estimated bytes one operator holds before spilling. `<= 0` removes the cap |
| `stats_top_values` | `100` | Most common values `ANALYZE` keeps per column. `0` keeps no list |
| `stats_plan_time_lookup_max_rows` | `2000` | Largest dimension table the join planner reads at plan time. `0` turns it off |

Turning a cost-based flag off keeps cost annotations and the heuristic
cross-product repair.

## Single-table access paths

The planner picks one of six scans:

1. Full table scan (`table-scan`).
2. End-to-end scan of a forced index (`table-scan ... forced-index=`), from
   `t@{FORCE_INDEX=idx}`, when no other scan applies.
3. Point lookup on a unique index (`index-lookup`).
4. Range scan on an index (`index-range-scan`).
5. `IN`-list probes (`index-in-list`), when repeated probes beat a wider scan.
6. GIN scan for a JSONB predicate (`gin-scan`).

### Heuristic scoring

- Full equality on a unique index beats every other candidate.
- Equality on a non-unique index, or an equality prefix, is a strong candidate.
- An equality prefix plus a range on the next column drives a composite range
  scan.
- An equality never loses an index because its value has no successor. That covers
  `DATE`, `DATETIME`, `FLOAT32`, `BYTES`, a value at its type's maximum and a
  descending column. These use an inclusive `[v, v]` range.
- An index whose prefix matches the `ORDER BY` can win without any filter.
- An `IN (...)` list on an indexed column competes with range and full scans.

### Cost-based selection

Runs when `cost_based_access_path_enabled` is on, the table has a positive
row-count estimate, and the table has analyzed statistics. A table never
analyzed keeps the heuristic choice. `UPDATE` and `DELETE` row-location scans
always use the heuristic path.

How a range scan is priced:

- The equality prefix is priced by distinct count: the prefix tuple's `key` row, else
  the column's count, else the product of column counts. With no count at all, it
  uses the `0.10` fallback.
- The next column is priced by its histogram over its bounds, else by interpolation
  between min and max, else by its distinct count when one value is pinned, else by
  fixed constants (one side `0.50`, two sides `0.10`).
- Equal-cost candidates are ordered: covering first, then more bound key columns,
  then the index name. So `a = ? AND b >= ?` keeps the index `(a, b)` over a
  prefix-only index on `a`.

The cost-based path falls back to a full scan when that is cheapest. The always-on
range-versus-full-scan veto applies at the 40 % breakeven.

### FORCE_INDEX

- `t@{FORCE_INDEX=idx}` first asks for the bounded scan `idx` can serve. When there is
  one, it is the plan. It does not compete on cost, and the veto does not apply.
- When the predicate cannot bound `idx`, the automatic choice runs as if there were no
  hint. The hint becomes an end-to-end scan only when no automatic scan is chosen.
- This happens with no comparison on the leading column, or a unique index that would
  omit rows with a `NULL` key.
- A name that is not a readable index of the table is an error.
- The hint is part of the plan-cache shape.

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
- An `IN` list of more than eight constant items runs once per statement into a hash
  set, in every position.
- A seek drops an equality on a bound column from the residual filter, for every
  column type.
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

### GIN indexes

```sql
CREATE INDEX events_payload_gin ON events USING GIN (payload);
```

- A GIN index has exactly one `jsonb` column. It has no `INCLUDE` and no `DESC`.
- It serves these top-level `WHERE` conjuncts when the other operand is a constant:
  `col @> doc`, `doc <@ col`, `col ? key`, `col ?| keys`, `col ?& keys`,
  `col ->> 'k' = text`, `col -> 'k' = doc`, and their function forms.
- It does not serve a conjunct under `OR` or `NOT`, or `col @> '{}'`, `'[]'` or a
  scalar. It does not serve `col ->> 'k' = text` when the text starts with `{` or `[`.
  It does not serve a join.
- The planner tries it when no ordered index narrows the scan. The estimate does not
  change the choice: it is the fallback `0.10` of the table.
- The full predicate stays as a `filter` above the `gin-scan`. The index gives a
  superset of the matching rows.
- A write costs one entry per distinct leaf of the document. These count against the
  transaction mutation limit.
- A GIN decision is never in the plan cache.

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

A `topk` normally stays in memory. With spill on, it moves to a disk selection only
when the kept rows pass `spill_threshold_bytes`. That selection writes at most about
`k` rows to disk.

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

## Window functions and set operations

- The window step runs after `WHERE`, `GROUP BY` and `HAVING`, and before the select
  list, `DISTINCT`, `ORDER BY` and `LIMIT`.
- It sorts once per distinct `(PARTITION BY, ORDER BY)`.
- A `LIMIT` never cuts the rows a window reads. Narrow with `WHERE`.
- A filter on a window result needs a derived table, and it runs after the window.
- The window inputs of the current partition stay in memory, even with spill on.
- `UNION ALL` streams its operands. A `LIMIT` stops them early. It never spills.
- `UNION` runs through `DISTINCT`. Prefer `UNION ALL` when duplicates cannot occur.
- `INTERSECT` and `EXCEPT` count the right operand's distinct rows in a hash table,
  then stream the left operand. `EXCEPT` also keeps the distinct left rows it returned.
- The planner does not estimate a set operation's result size.
- A set-operation `ORDER BY` takes an output column name or position. To sort by an
  expression, wrap the set operation in a derived table.

## Joins

Supported: `JOIN`, `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN` (run as a swapped
left join), `CROSS JOIN`, comma joins, and a set-returning function on either side.
Not supported: `FULL [OUTER] JOIN`,
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
| Index nested loop | `0.65 × L + probes × (probeWeight + matchesPerProbe)` |
| Hash join | `S + L + 0.1 × min(L, R)` |
| Nested loop | `S + 0.1 × max(1, L) × max(1, R)` |

An available index lookup wins unless hash is strictly cheaper. Merge join is not
costed during order search.

Index nested loop terms:

- `probes` is the distinct keys expected among the left rows that hold a key. With `r`
  keyed rows of a table of `N` rows and `d` distinct values, it is
  `d × (1 − (1 − r/N)^(N/d))`. A NULL key probes nothing.
- `probeWeight` is `3` for a unique index and `16` for a non-unique index.
- `matchesPerProbe` is `1` for a unique index, else right rows over the column's
  distinct count, or the named keys' share when a plan-time lookup knows them.
- A popular key of a skewed column returns more rows than `matchesPerProbe`.

Index nested loop execution:

- It probes a page of left rows at a time. A page's distinct keys cost one batched
  index read and one batched row read. Rows still come out in left-row order.
- It keeps the right rows of each probed key in a memo, up to 65,536 rows. A repeated
  key reads nothing. The `ON` predicate still runs for each pair.
- When several `ON` conjuncts could drive the lookup, a unique index wins over a
  non-unique one, whatever their order.

- Hash join build side: smaller estimate for inner joins; always the right
  table for left outer joins.
- Hash build above `HashJoinMaxBuildRows` (default 1,000,000): Grace hash join
  with spill on; nested-loop fallback with spill off.
- Merge join buffers only the current run of equal keys.

### Join order

- Cost-based search: connected left-deep orders, dynamic programming over reachable
  table sets, at most 30 tables. Needs loaded row counts for all base tables.
- Past 2,000,000 costed steps it takes a greedy order: the fewest rows after the filter
  first, then the connected table with the smallest estimated result. The step count
  depends only on the join graph, so the same query takes the same path each time.
- Above 30 tables, or when an order needs a cross product, the heuristic plans the
  join. `plan-info` shows which path ran under `EXPLAIN (ANALYZE)`.
- Never enumerates a cross product or bushy trees.
- Heuristic order scores each table: 0 for a unique-index equality, 1 for
  another indexed predicate, 2 for none. Lower scores go first.
- Cross-product repair works without statistics, up to 64 sources.
- An outer join anywhere in the tree keeps the written order.
- A volatile condition, such as one using `random()`, restricts movement.
- Join-key equality is estimated as `1 / max(NDV_left, NDV_right)`. Without
  counts the fallback selectivity is `0.1`.
- NULL keys match nothing. The estimate keeps only each side's share of rows that hold
  a key.
- A single-column key, unique on neither side, with a histogram on both, uses the two
  value distributions. This catches both sides concentrating on the same values.
- If the join key contains every column of a unique index on one side, that side
  contributes at most one match per row.

### Plan-time lookup

A filter such as `it.info = 'release dates'` can name one row of a small table. The
planner reads that row before the join search when all of these hold:

- The table has a single-column unique key.
- It has at most `stats_plan_time_lookup_max_rows` rows.
- The filter has an equality or an `IN` list on constants.
- At most 64 rows are kept.

The search then prices probes at the named keys' real share of the fact column.
`EXPLAIN` reads the same keys, so it reads that small table too.

### Join tuning

- Index the right-side join key, for example `posts(user_id)` for
  `JOIN posts p ON p.user_id = u.id`.
- Index both join keys of a large equi-join to make a merge join possible.
- Filter early. A filter on the preserved side of an outer join pushes down.
  A filter on the null-extended side stays above the join.
- Qualify column names in joins.
- Under Serializable read-write, a join leaf with a pushed filter no index serves holds
  a shared lock over the whole table until the transaction ends. Index the filter
  column. An index leaf locks only the keys it reads.

## Subqueries

| Shape | Plan |
| --- | --- |
| `x IN (SELECT key FROM t)`, indexed inner column | `semi-join` with index probes |
| `x NOT IN (SELECT key FROM t)`, indexed `NOT NULL` inner column | `anti-join` |
| Same, nullable inner column | `null-aware-anti-join` |
| Inner column without an index | Subquery materialized; no join node |
| Correlated `EXISTS` with an index on the correlated equality columns | Indexed seek per outer row |
| Correlated `EXISTS` without such an index | Full inner scan per outer row |
| Uncorrelated subquery of any kind, in any expression position | Evaluated once, result folded into the outer plan |
| Derived table in `FROM` | `derived-table-scan`, materialized once |

Position rules:

- Uncorrelated subqueries run in the select list, `HAVING`, `JOIN ON`, `CASE`,
  `BETWEEN` bounds and function arguments. `UPDATE`, `DELETE`, `INSERT VALUES` and
  `ON CONFLICT DO UPDATE` accept them too.
- Subqueries inside a derived table and inside a set-operation operand go through the
  same pipeline.
- A correlated `EXISTS` works only in the `WHERE` of a `SELECT`, at any depth.
  Elsewhere it fails with `CADB0533`.
- A correlated `IN`, `NOT IN` or quantified comparison is refused everywhere. A
  correlated scalar subquery is refused as an unknown alias.
- `ORDER BY` and `GROUP BY` refuse a subquery with `CADB0400`.
- In a join, a `WHERE` conjunct with a correlated `EXISTS` is not pushed to one table's
  scan. It runs after the join, so it does not narrow a join input.
- A semi-join evaluates its inner `WHERE` per probe, in the subquery's own name scope.
  It can qualify a column with the inner alias.

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
| Histograms, distinct counts per column and per composite key prefix, most common values, `NULL` share | `ANALYZE` (manual or automatic) |

- `ANALYZE` collects the leading column of every index, then other columns up to
  `stats_analyze_max_columns`. A filter on an unindexed column can therefore get a
  real estimate.
- Equality estimate on a heavy value: that value's share from the most common values.
- Equality estimate on any other value: `(1 − heavy shares) / (NDV − heavy count)`, at
  least one row. With no heavy value this is `1 / distinct_count`.
- Range estimate: from the histogram. An inclusive bound on a heavy value carries its
  whole mass.
- `col LIKE 'abc%'`: the histogram range of the prefix.
- Other `LIKE` and `ILIKE` patterns: the share of histogram bucket bounds that match.
  The fallback applies without a histogram. `NOT LIKE` is `1 −` that share.
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
- Every statistics publish, manual or background, bumps the table's analyze generation
  on that node. The next lookup of an affected shape misses and plans again. A running
  statement keeps its plan.
- Another node loads a table's statistics once and keeps them until the entry is
  evicted or the node restarts. Until then it plans with the older statistics.
- A single-table entry keeps the estimate it was made for. A hit re-estimates. If the
  `WHERE` selectivity or the `LIMIT` demand moved by more than 4 times, it plans again.
- Risk: a decision made for one literal can still be slow for another when the
  estimate does not move. Disable the cache to compare against a fresh plan.
- Distributed (fragment) plans never come from the plan cache.

## Known planner limits

- Statistics are estimates. Skew estimates are one pair of columns at a time, and a
  cross-table correlation that no column statistic shows still misleads the model.
- `EXPLAIN (LOGICAL)` shows the physical tree.
- Descending use of an index is limited to indexes declared `DESC`.
- `COUNT(DISTINCT ...)` is not supported.
- Interesting orders are not kept in the join search. It does not keep a costlier
  sub-plan for an order a parent needs.
- A tree with an outer join gets no search. It keeps its written order.
- When the query writes only `a.x = b.x AND b.x = c.x`, no edge joins `a` and `c`
  directly.
- A heuristic or repaired join input can get a fixed 10,000-row fallback
  estimate.

Sources: https://camusdb.github.io/docs/query-planning,
https://camusdb.github.io/docs/query-planner-internals,
https://camusdb.github.io/docs/sql-indexes,
https://camusdb.github.io/docs/show-statistics,
https://camusdb.github.io/docs/automatic-analyze,
https://camusdb.github.io/docs/spill-to-disk,
https://camusdb.github.io/docs/json,
https://camusdb.github.io/docs/sql-queries,
https://camusdb.github.io/docs/sql-expression-syntax
