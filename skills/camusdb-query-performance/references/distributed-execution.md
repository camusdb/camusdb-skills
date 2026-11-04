# Distributed execution, ranges and routing

Read this file when a slow query runs on a CamusDB cluster, when key-range
sharding is on, or when a client could route statements better.

## Placement basics

- Hash routing is the default. A table's rows and all its secondary indexes
  live on one Raft partition.
- Key-range sharding (`key_range_sharding: true`) routes each key space by key
  order. Rows and each secondary index are separate key spaces. Each starts as
  one range and can split.
- Rows are stored under an internal ObjectId row id, not the primary key. The
  primary key is a unique index mapping keys to row ids.
- A cluster records its routing mode when it is created. A node with
  `key_range_sharding` unset follows the record. A node set to the other mode
  refuses to start. To change the mode, dump the data and reimport it into a new
  cluster.
- Set `initial_partitions: 2` or more to spread work.
- A table's first range starts on the partition that hash routing would use. A new
  index or large-value space starts with the table's other spaces. A table stays on
  one partition until its first split.
- Every secondary index can split, whatever its key column types, `String` included.
  Only an index whose key columns cannot be resolved stays hash-routed.
- Under key-range sharding, only a Serializable read-write transaction locks its
  scans. A Serializable read-only transaction, an autocommit `SELECT` and a scan in
  an explicit Read Committed transaction take no range lock.
- Automatic splits are off until you set `kahuna.range_split_threshold` or the
  load-branch settings.

## Distributed query execution

```yaml
key_range_sharding: true
distributed_query_execution: true
```

| Setting | Default | Scope | Meaning |
| --- | --- | --- | --- |
| `key_range_sharding` | `false` | cluster, restart | Required |
| `distributed_query_execution` | `false` | cluster, restart | Enables fragments |
| `max_query_parallelism` | `1` | node, runtime | Decode workers per full scan; works without a cluster |
| `broadcast_join_max_build_rows` | `10000` | node, runtime | Largest hash build side to broadcast; `0` disables |

Runtime settings change live:

```sql
SET CLUSTER SETTING max_query_parallelism = 4;
SET CLUSTER SETTING broadcast_join_max_build_rows = 50000;
```

Fill `http_peers` beside `peers` in a cluster. Fragments travel over HTTP.

### Eligibility

A scan becomes a `gather` only when all hold:

- It is a full scan of primary rows. Index lookups, index range scans and
  forced-index scans stay local.
- The node is in cluster mode with key-range sharding on.
- The table placement has more than one span.
- The transaction is not optimistic.
- It is not the read half of an `UPDATE` or `DELETE`.

A peer receives a span only when its filter is shippable. Not shippable:

| Expression | Reason |
| --- | --- |
| Subquery, `EXISTS` | Would re-enter the coordinator executor |
| Parameter placeholder such as `@p` | Bound values live on the coordinator |
| Volatile function such as `NOW()` | Must evaluate once, in one place |

A query with no filter, and a result-cache candidate, also scan locally.

With `LIMIT`, each span stops after `limit + offset` surviving rows.

### Aggregates

`COUNT`, `SUM`, `MIN`, `MAX` and `AVG` run per span and merge on the
coordinator. CamusDB declines the split for:

- A `HAVING` clause.
- An expression as a group key.
- A global aggregate with more than one projection, and a global `AVG`.
- `EXPLAIN (ANALYZE)`.
- Result-cache dependency collection.

### Broadcast hash join

After the build phase, a build side of 1 to `broadcast_join_max_build_rows`
rows is sent to the leaders of the probe table's spans. Peers probe locally.
Conditions:

- The probe side is a base table of primary rows with several spans and at least
  one remote leader.
- The `ON` predicate and the probe filter are shippable.
- The join is inner. Left outer joins never broadcast.
- The query is not collecting cache dependencies, folding reads through one
  session, or taking exclusive predicate locks.

The decision uses the true build row count at run time.

### Reading the plan

```sql
EXPLAIN SELECT * FROM orders WHERE status = 'open';
```

```text
physical  distribution  distributed=yes
physical  gather        gather(spans: 4, remote_leader_fraction: 0.75)
physical  table-scan    table=orders
```

`distributed=no (<reason>)` reasons:

- `key-range sharding is off`
- `not a primary-row full scan`
- `standalone node`
- `placement has a single span`
- `transaction folds reads (optimistic read-set needs one session stream)`
- `exclusive predicate locks (DML write path)`

`EXPLAIN (ANALYZE)` adds one `gather-span` row per span. A broadcast join adds
`broadcast-span` rows with the same columns:

```text
analyze  gather-span  partition 1: mode=local, rows_delivered=25000
analyze  gather-span  partition 2: mode=remote, node=10.0.0.2:7070, rows_delivered=31, rows_shipped=31, remote_rows_scanned=24180
analyze  gather-span  partition 3: mode=remote-fallback, node=10.0.0.3:7070, rows_delivered=24903, rows_shipped=12
```

| Mode | Meaning |
| --- | --- |
| `local` | Coordinator scanned the span; filter runs above the gather |
| `remote` | Peer filtered; only survivors shipped |
| `remote-fallback` | Peer failed after shipping `rows_shipped`; coordinator finished |

`actual_rows` carries `rows_delivered`. `rows_read` carries
`remote_rows_scanned`.

A window function runs on the node that runs the statement, over the gathered rows.
The inner query ships its scans and partial aggregates as any query does.

### Counters

```sql
SHOW ENGINE STATS LIKE 'distributed.%';
```

| Metric | Meaning |
| --- | --- |
| `distributed.fragments_dispatched` | Remote fragments started as coordinator |
| `distributed.fragment_fallbacks` | Remote fragments that failed and finished locally |
| `distributed.fragments_served` | Fragments executed for a peer |
| `distributed.partial_aggregate_gathers` | Aggregations split per span |
| `distributed.rows_shipped_in` | Rows received from peers |
| `distributed.rows_shipped_out` | Rows returned to peers |

Counters are cumulative. Sample twice and subtract for a rate. Absent rows mean
distribution is off.

### Tuning checklist

1. Confirm `distribution distributed=yes`. If not, read the reason.
2. Replace parameterized filters with literals only when you need peer-side
   filtering, and the statement text can vary.
3. Check `gather-span` for `remote-fallback`. Repeated fallbacks on one peer
   point to that node.
4. Raise `max_query_parallelism` for wide rows on full scans.
5. Prefer index access for selective queries. Index scans are not distributed,
   but they read far less.

## SHOW RANGES

```sql
SHOW RANGES FROM TABLE users;
SHOW RANGES FROM INDEX users@users_pkey;
SHOW RANGE FROM TABLE users FOR ROW (1500);
SHOW RANGE FROM INDEX users@by_email FOR ROW ('a@example.com');
```

Use it to answer: has the key space split, which partition serves each range,
which range holds a hot key, and is the leader local.

Key columns: `relation`, `routing` (`key_range` or `hash`), `span`,
`start_key`, `end_key`, `partition_id`, `generation`, `leader`,
`leader_is_local`, `hosted_locally`, `replicas`, `probe_key`.

- Compare `partition_id` across runs, not `span`. Splits renumber spans.
- A hash-routed key space shows one unbounded span with `generation` `0`.
- `t@~pk`, `t@t_pkey` and `t@primary` name the primary index.
- `FOR ROW` on a table errors when no row has that primary key.
- The output is this node's view. A lagging node can show an older generation.
- Needs `SELECT` on the table.

Split counters:

| Counter | Meaning |
| --- | --- |
| `kahuna.range.splits` | Splits committed |
| `kahuna.range.split.indivisible_refusals` | One hot key held the load |
| `kahuna.range.split.no_relief_skips` | No peer could host the child |
| `kahuna.range.split.settle_skips` | Child still in its settle window |
| `kahuna.range.merge.warm_skips` | Merge candidate still too warm |

## Hotspots under key-range sharding

Always-increasing keys send every insert to the last range:

- Sequences, `SERIAL`, `BIGSERIAL`, identity columns.
- A `DATE` or `DATETIME` first key column.
- `OID` from `gen_id()` and `UUID` from `gen_uuid_v7()`.

Use `gen_uuid_v4()` for high insert rates. Lead secondary indexes with a
well-spread column such as `tenant_id` or `player_id`. There is no hash-sharded
index and no bit-reversed sequence.

Writes during a range move fail retryably with `CADB0504` or `CADB0502`.
Autocommit retries on the server. Explicit transactions restart from `BEGIN`.

## SQL routing advice

A multi-endpoint client can learn which node leads a statement's data and send
later executions there. This removes a forwarding hop.

- Opt in per request: gRPC `SqlRequest.routing_accept_version = 1`, REST
  `routingAcceptVersion: 1`.
- Server settings: `sql_routing_advice_enabled: true` (default),
  `sql_routing_advice_ttl_ms: 5000` (range 1 to 600000).
- Eligible: simple single-table `SELECT`, `INSERT`, `UPDATE`, `DELETE` on a table
  whose every key space sits on one partition. Hash routing always meets this.
- Key-range routing meets it while the row space, every index space and the
  large-value space are each one range on one partition. The first split ends it.
- Not eligible: joins, subqueries, derived tables, views, time-travel reads,
  branches, materialized views, cache-hinted queries, DDL, `SHOW`, and a key-range
  table with a divided space.
- Advice never moves a live transaction. The .NET driver and TypeScript
  connector can defer `BEGIN` to the first statement when learned routing is on.
- Under key-range routing, a deferred `BEGIN` pins the session to the partition of
  the table's one row range. A table with a divided row space gets an unpinned
  session.
- `prefer` reasons: `singleTableHash`, `singleRangeTable`.
- `clear` reasons: `ineligible`, `placementUnknown`, `cacheAffinity`.

Sources: https://camusdb.github.io/docs/distributed-queries,
https://camusdb.github.io/docs/key-range-sharding,
https://camusdb.github.io/docs/show-ranges,
https://camusdb.github.io/docs/sql-routing-advice,
https://camusdb.github.io/docs/best-practices
