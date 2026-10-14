# Observability reference

Read this file when you need the exact columns, settings, metric names, or
permissions of a CamusDB diagnostic tool.

## Contents

- Tool summary
- SHOW QUERIES and CANCEL QUERY
- SHOW CONNECTIONS
- SHOW SLOW QUERIES
- SHOW ENGINE STATS
- SHOW VARIABLES
- Operator dashboard
- Prometheus and OpenTelemetry

## Tool summary

| Tool | Answers | Scope | Needs superuser (auth on) | Off by default |
| --- | --- | --- | --- | --- |
| `SHOW QUERIES` | What runs now, for how long | Node; `SHOW CLUSTER QUERIES` for all | No; users see their own rows | No |
| `SHOW CONNECTIONS` | Which client sockets are open, idle, or busy | Node; `SHOW CLUSTER CONNECTIONS` for all | No; users see their own rows | No |
| `CANCEL QUERY '<id>'` | Stops a running read | Any node forwards it | No; users cancel their own | No |
| `SHOW SLOW QUERIES` | Which statements crossed a threshold, and why | Node | Yes | Yes |
| `SHOW ENGINE STATS` | Kahuna and Kommander metrics since process start | Node | Yes | No |
| `SHOW VARIABLES` | Effective configuration and its source | Node | Yes (`CADB0517` otherwise) | No |
| Operator dashboard | Browser view of the above | Node | Four panels | No |
| Prometheus/OTel | History, alerting, traces | Standalone node | Protect the endpoint | Yes |

All `SHOW` statements above are node-local unless they say `CLUSTER`. Run them
against each node.

## SHOW QUERIES and CANCEL QUERY

```sql
SHOW QUERIES;
SHOW QUERIES LIKE '%FROM orders%';
SHOW CLUSTER QUERIES;
CANCEL QUERY 'b71a90e314-45';
```

| Column | Meaning |
| --- | --- |
| `query_id` | The id for `CANCEL QUERY`. Unique across the cluster. |
| `node` | Node running the statement. |
| `connection_id` | Links to `SHOW CONNECTIONS`. |
| `client_address` | Client IP. |
| `transport` | `http`, `grpc`, `grpc-stream`, or `embedded`. |
| `user_name` | Authenticated user, or `NULL` with auth off. |
| `database_name` | Context database. |
| `transaction_id` | Explicit transaction handle, when inside one. |
| `kind` | Statement kind, such as `select`, `update`, `create_table`. |
| `phase` | `planning` before the first row, then `executing`. |
| `started_at` | UTC start. |
| `elapsed_ms` | Run time so far. |
| `rows_returned` | Rows the client has read so far. |
| `cancellable` | Whether `CANCEL QUERY` can stop it now. |
| `cancel_requested` | Whether a cancel is pending. |
| `sql` | Text, credentials masked, length-limited. |
| `error` | Set only in cluster forms when a node did not answer. |

Facts:

- Rows are oldest first. `SHOW QUERIES` lists itself.
- A row-returning statement stays listed until its cursor ends. A slow reader
  shows a growing `rows_returned`.
- Only reads (`SELECT`, `SHOW`, `EXPLAIN ANALYZE`) are cancellable. They stop at
  the next row boundary. Writes and schema changes run to commit or rollback.
- The cancelled client gets `CADB0554`. Buffered rows may still arrive first.
- Not listed: typed row APIs (`/query`, `/insert`, `/update`, `/delete`, gRPC
  `CamusRows`), worker-side distributed scans, and idle explicit transactions.
- There is no `KILL CONNECTION`.

Runtime settings (change with `SET CLUSTER SETTING`):

| Setting | Default |
| --- | --- |
| `query_activity_enabled` | `true` |
| `query_activity_max_sql_length` | `1024` |
| `cluster_activity_peer_timeout_ms` | `2000` |

## SHOW CONNECTIONS

| Column | Meaning |
| --- | --- |
| `connection_id` | Referenced by `SHOW QUERIES.connection_id`. |
| `node` | Node holding the connection. |
| `client_address` | Client IP. |
| `protocol` | `HTTP/1.1`, `HTTP/2`, or `HTTP/3`. gRPC is `HTTP/2`. |
| `kind` | `client`, or `peer` for another node. |
| `user_name` | User of the last statement. |
| `opened_at`, `age_ms` | When and how long open. |
| `idle_ms` | Time since the last request; `0` while a statement runs. |
| `requests` | Requests started on the connection. |
| `active_queries` | Statements running now. |
| `open_streams` | Open `BatchExecute` streams. |
| `error` | Cluster forms only. |

One gRPC channel usually appears as one row, even with many concurrent calls.

## SHOW SLOW QUERIES

Enable in `config.yml`:

```yaml
slow_query_log_enabled: true        # default false
slow_query_log_threshold_ms: 1000   # 0 records every statement
slow_query_log_max_entries: 200
slow_query_log_max_sql_length: 4096
```

```sql
SHOW SLOW QUERIES;
SHOW SLOW QUERIES LIKE '%orders%';
SHOW VARIABLES LIKE 'slow_query%';
```

| Column | Meaning |
| --- | --- |
| `seq` | Recording order. Keeps rising after the ring wraps. |
| `started_at` | UTC start. |
| `duration_ms` | Wall-clock duration. |
| `database`, `user`, `kind` | Context. |
| `rows_returned` | Rows returned, or rows affected. |
| `rows_read` | Rows fetched before filtering, all plan sides. |
| `full_scan` | Some part read a whole relation. |
| `spilled` | A blocking operator spilled to disk. |
| `outcome` | `completed`, `abandoned`, or `failed`. |
| `error_code` | Code for a failed statement. |
| `truncated` | SQL text was shortened. |
| `sql` | Text, password literals redacted. |

Facts: newest first; memory only, lost on restart; node-local; not an audit
log; `SHOW SLOW QUERIES` itself is never recorded. If `seq` jumped by more than
the ring size between reads, entries were overwritten.

## SHOW ENGINE STATS

```sql
SHOW ENGINE STATS;
SHOW ENGINE STATS LIKE 'raft.executor%';
SHOW ENGINE STATS LIKE 'kahuna.kv.write%';
SHOW ENGINE STATS LIKE 'kahuna.tx_admission%';
SHOW ENGINE STATS LIKE 'kahuna.range%';
```

| Column | Meaning |
| --- | --- |
| `node` | Local Raft endpoint. |
| `source` | `kommander` or `kahuna`. |
| `metric` | Instrument name. |
| `tags` | Sorted `k=v` pairs. |
| `kind` | `counter`, `histogram`, or `gauge`. |
| `count` | Counter total, histogram observations, or `1` for a gauge. |
| `total` | Counter total or histogram sum. `NULL` for a gauge. |
| `min`, `max`, `last` | Histogram distribution. `last` for a gauge. `NULL` for a counter. |

Reading rules:

- Counters and histograms accumulate from process start. Take two readings and
  subtract.
- A gauge is sampled now and has no history.
- Average = `total / count`. A `max` far above the average with a normal `last`
  means a periodic stall, not sustained load.
- With `engine_metrics_enabled: false`, the statement returns zero rows.

Useful metrics:

| Metric | Signal |
| --- | --- |
| `raft.executor.operation_duration_ms` | Dispatch latency per partition and operation class. |
| `raft.executor.operations_total` | Dispatch volume. Compare with the latency jump. |
| `raft.executor.rejections_total` | Proposals refused because a partition queue was full. Sustained above zero means overload. |
| `raft.wal.batch_size` | WAL batch efficiency. Mean near 1 means no coalescing. |
| `raft.wal.*` | WAL flush behavior. |
| `raft.heartbeat_delay_ms` | Leader scheduling pressure. Far above the heartbeat interval means CPU shortage. |
| `raft.elections_started_total` | Leadership churn. |
| `kahuna.kv.write.batches`, `kahuna.kv.write.entries` | Write aggregation. Entries / batches = coalescing factor. |
| `kahuna.backup.*` | Backup or PITR cycles. |
| `kahuna.tx_admission.queued` | Waiters at the admission gate, by priority. Zero means the gate does not bind. |
| `kahuna.tx_admission.in_flight`, `max_queue_depth`, `admitted`, `aged_promotions`, `abandoned_while_waiting`, `rejected_queue_full` | Admission detail. |
| `kahuna.range.splits`, `kahuna.range.split.indivisible_refusals`, `kahuna.range.split.no_relief_skips`, `kahuna.range.split.settle_skips`, `kahuna.range.merge.warm_skips` | Range splitter activity, with key-range sharding. Pair with `SHOW RANGES`. |

The `kahuna.collect.*` metrics arrive only in a Kahuna release after `0.9.8`.
Until CamusDB uses that release, a collection tick shows only as an unexplained
gap.

## SHOW VARIABLES

```sql
SHOW VARIABLES;
SHOW VARIABLES LIKE 'ttl_%';
SHOW VARIABLES LIKE 'kahuna.%';
```

| Column | Meaning |
| --- | --- |
| `variable` | `snake_case` key. `kahuna:` keys appear dotted, such as `kahuna.wal_sync_writes`. |
| `value` | Effective value. `NULL` when unset. |
| `type` | `bool`, `int`, `long`, `double`, `string`, `enum`, `duration_ms`, `list`. |
| `default` | Built-in default. A different `value` was configured. |
| `source` | `default`, `config`, `env`, `cli`, or `cluster`. Precedence: cluster, cli, env, config, default. |
| `mutability` | `runtime` applies without restart. `restart` applies only after restart. |
| `scope` | `cluster` (nodes must agree) or `node`. |

Facts:

- `LIKE` is case-sensitive. All names are lowercase.
- Secrets render as `********` when set.
- Not listed: `mode`, `node_name`, ports, `peers`, `http_peers`, certificate
  paths, the `diagnostics:` section, and computed values.
- Read-only. Change with `SET CLUSTER SETTING name = value`. Revert with
  `RESET CLUSTER SETTING name`. A `config.yml` edit needs a restart.
- A `restart` setting changed at runtime shows the new value, but the node obeys
  the old one until it restarts.

## Operator dashboard

Open the node's HTTP port in a browser.

```yaml
dashboard_enabled: true          # default
dashboard_refresh_seconds: 2     # clamped 1..300
```

Panels: Node, Load, Engine, Cluster, Databases, Relations, Backups, Slow
queries, Configuration, Overlay (`SHOW CLUSTER SETTINGS`).

- Read-only. No DML, no DDL.
- Node-local. Counters accumulate; rates appear after the second refresh.
- No history and no alerts.
- Engine, Configuration, Slow queries, and Cluster need a superuser.
- With auth enabled, sign-in needs HTTPS or loopback (the cookie is always
  `Secure`). With auth disabled, the dashboard is loopback-only.
- A disabled feature shows as disabled in its panel, not as an error.

## Prometheus and OpenTelemetry

Standalone node only. Off by default:

```yaml
diagnostics:
  enabled: true
  prometheus_enabled: true
  prometheus_path: /metrics
  otlp_endpoint:                 # optional OTLP collector URL for traces
  trace_sample_ratio: 0.01
  include_runtime_metrics: true
```

Server metrics (Prometheus names):

| Metric | Type | Tags |
| --- | --- | --- |
| `camus_request_count_total` | counter | `operation`, `transport`, `outcome` |
| `camus_request_duration_milliseconds` | histogram | `operation`, `transport`, `outcome` |
| `camus_request_in_flight` | up-down | `transport` |
| `camus_execute_duration_milliseconds` | histogram | `operation`, `statement` |
| `camus_sql_parse_cache_total` | counter | `result` |
| `camus_sql_parse_duration_milliseconds` | histogram | none |
| `camus_query_rows_total` | counter | `scan`, `stage` |
| `camus_query_scan_duration_milliseconds` | histogram | `scan` |
| `camus_query_cache_requests_total` | counter | `result` |
| `camus_transaction_count_total` | counter | `operation`, `outcome` |
| `camus_transaction_active` | up-down | `transaction_mode` |
| `camus_transaction_commit_duration_milliseconds` | histogram | `outcome` |
| `camus_transaction_staged_mutations` | histogram | none |

The exporter also carries Kahuna, Kommander, row-level TTL, and .NET runtime
metrics. Protect the endpoint; it has no authentication.

`SHOW ENGINE STATS` is independent of this section. It is controlled by
`engine_metrics_enabled`.

Docs:

- https://camusdb.github.io/docs/query-activity
- https://camusdb.github.io/docs/slow-query-log
- https://camusdb.github.io/docs/engine-stats
- https://camusdb.github.io/docs/show-variables
- https://camusdb.github.io/docs/operator-dashboard
- https://camusdb.github.io/docs/performance-diagnostics
