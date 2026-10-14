---
name: camusdb-troubleshooting
description: Diagnoses CamusDB errors and runtime problems. Explains the cause, the fix, and the retry rule for any CADBxxxx error code, such as CADB0502 TransactionConflict, CADB0504 TransactionMustRetry, CADB0509 TransactionFinalizeUnresolved, CADB0506, CADB0503 SchemaCatchingUp, CADB0536 InsufficientDiskSpace, CADB0549, or CADB0517. Guides diagnosis of slow, stuck, or overloaded workloads with SHOW QUERIES, SHOW CLUSTER QUERIES, SHOW CONNECTIONS, CANCEL QUERY, SHOW SLOW QUERIES, SHOW ENGINE STATS, SHOW VARIABLES, Prometheus and OpenTelemetry diagnostics, and the operator dashboard. Use when a user pastes a CamusDB error, an HTTP "status":"failed" body, a gRPC camus-error-code trailer, or a Kommander "Slow dispatch" warning, or reports latency spikes, hung queries, conflict storms, refused writes, or nodes that disagree.
---

# CamusDB troubleshooting

This skill turns a CamusDB error or symptom into a cause and a fix. It covers
every `CADBxxxx` code and the built-in observability tools. Query-plan tuning is
out of scope here. For plans and indexes, use the `camusdb-query-performance`
skill. For writing retry code, use the `camusdb-transactions` skill.

## Workflow

1. **Capture the facts.** Get the exact code, the message, the statement, the
   transport (HTTP, gRPC, driver), the node, and whether the statement ran in an
   explicit transaction or in autocommit.
2. **Error code present?** Look it up in
   [references/error-codes.md](references/error-codes.md). State the name, the
   cause, the fix, and the retry class.
3. **Apply the retry class.** Replay, retry, resend the finalize, fix first, or
   escalate. See "Retry classes" below.
4. **No code, or a performance symptom?** Match the symptom in the triage table
   below. Read [references/symptom-playbooks.md](references/symptom-playbooks.md)
   for the step-by-step checks.
5. **Collect evidence per node.** Most tools are node-local. Run them on each
   node, or use the `CLUSTER` forms where they exist.
6. **Escalate corruption codes.** For `Operator` codes, collect logs and the
   tool output. Do not suggest blind retries.
7. **Explain the fix in CamusDB terms.** Check the "Common mistakes" list before
   you suggest a habit from another database.

## Where the code appears

| Surface | Location |
| --- | --- |
| HTTP | `{"status": "failed", "code": "CADB0502", "message": "..."}` |
| HTTP streaming | The last trailer line, possibly after HTTP `200` |
| gRPC unary or streaming | The `camus-error-code` trailer |
| gRPC batch | The `BatchError` message of the operation |
| .NET driver | `CamusException.Code` |
| Slow query log | The `error_code` column of a failed entry |

## Code families

| Prefix | Area |
| --- | --- |
| `CADB00xx` | Catalog, metadata, storage state. Many are corruption signals. |
| `CADB03xx` | Integrity constraints: unique, not null, check, foreign key. |
| `CADB04xx` | Invalid SQL, input, or expression shape. |
| `CADB05xx` | Transactions, schema catch-up, prepared statements, auth, views, sequences, query cancel. |
| `CADB06xx` | Configuration validation. |
| `CADB07xx` | Backup and point-in-time recovery. |

## Retry classes

| Class | Codes | Action |
| --- | --- | --- |
| Replay | `CADB0502`, `CADB0504`, `CADB0505` | Roll back. Replay the whole transaction from `BEGIN`, with backoff. |
| Finalize | `CADB0509` | Resend the same `COMMIT` or `ROLLBACK` for the same transaction id. Never replay. |
| Retry | `CADB0503`, `CADB0520` (after re-prepare), `CADB0532`, `CADB0534`, `CADB0535`, `CADB070A`, `CADB070D` | Retry after the condition clears. |
| After fix | `CADB0516`, `CADB0518`, `CADB0536` | Fresh credentials, wait out the rate limit, or free disk. Then retry. |
| Fix first | `CADB0506` and most `03xx`, `04xx`, `05xx` codes | Change the request, data, or schema. A retry fails again. |
| Operator | `CADB0014`, `CADB0099`, `CADB0701`, `CADB0704`, `CADB0706`, `CADB070F` | Investigate. Collect logs. |

## The most common errors

**`CADB0502 TransactionConflict`.** A lock conflict or a conflicting write.
The message samples the contended keys. Replay from `BEGIN`. If it is frequent,
find the hot row and shard it, and shorten transactions.

**`CADB0504 TransactionMustRetry`.** A transient condition exhausted the
server's internal retries: admission, routing, leader change, transport,
lock-wait deadline, or an unservable range. Nothing was written. Replay with
backoff. If it spikes, check admission gauges and elections.

**`CADB0505 TransactionLifetimeExceeded`.** A serializable read-write
transaction stayed open past 1 hour. Replay and shorten it.

**`CADB0509 TransactionFinalizeUnresolved`.** The commit outcome is unknown
after 15 seconds (`transaction_finalize_retry_budget_ms`) of server retries. The
commit may have applied. Resend the same finalize. A replay risks applying the
work twice.

**`CADB0506 TransactionMutationLimitExceeded`.** The transaction exceeds
20,000 row and index mutations. A retry fails again. Split the work into
batches, or use `TRUNCATE` to empty a table.

**`CADB0503 SchemaCatchingUp`.** The node is more than one schema version
behind after DDL. It rejects reads and DML for that database until it catches
up. Retry later or on another node.

**`CADB0536 InsufficientDiskSpace`.** Free space is below
`min_free_disk_bytes`. Writes are refused; reads and DDL still work. Free disk,
then retry.

**`CADB0517 InsufficientPrivilege`.** With auth on, `SHOW ENGINE STATS`,
`SHOW VARIABLES`, and `SHOW SLOW QUERIES` need a superuser.

**`CADB0010 DatabaseDoesntExist`.** The database was never created, or the
caller has no grant on it. Both cases return the same code on purpose.

**`CADB0538 StatementNotAllowedInTransaction`.** `TRUNCATE` ran inside
`BEGIN`. Run it in autocommit.

## Triage by symptom

| Symptom | First tool | Playbook |
| --- | --- | --- |
| Many `CADB0502` | Error message key sample, `SHOW QUERIES` | 1 |
| `CADB0504` spikes | `SHOW ENGINE STATS LIKE 'kahuna.tx_admission%'` | 2 |
| Locks held, work blocked | `SHOW QUERIES`, `SHOW CONNECTIONS` | 3 |
| One query slow now | `SHOW QUERIES`, then `EXPLAIN` | 4 |
| Queries were slow earlier | `SHOW SLOW QUERIES` | 5 |
| Latency spikes once a minute, "Slow dispatch" log lines | `SHOW ENGINE STATS LIKE 'raft.executor%'`, twice | 6 |
| Node saturated | `raft.executor.rejections_total`, `raft.heartbeat_delay_ms` | 7 |
| Writes refused | Disk free space, `CADB0536` | 8 |
| Spill errors | `CADB0507`, `CADB0549` | 9 |
| Reads and DML refused after DDL | `CADB0503` | 10 |
| Errors after restart or failover | Logs, `CADB0504`, `CADB0509`, `CADB0535` | 11 |
| Nodes behave differently | `SHOW VARIABLES` on each node | 12 |
| Wrong results, no error | Quoting, TTL, isolation | 13 |

## Observability tools

```sql
-- What runs now (node, or every node)
SHOW QUERIES;
SHOW CLUSTER QUERIES;
SHOW CONNECTIONS;
CANCEL QUERY 'b71a90e314-45';        -- reads only

-- What was slow (enable slow_query_log_enabled first)
SHOW SLOW QUERIES LIKE '%orders%';

-- Engine metrics since process start (take two readings, subtract)
SHOW ENGINE STATS LIKE 'raft.wal%';

-- Effective configuration and where each value came from
SHOW VARIABLES LIKE 'slow_query%';
```

Rules for reading them:

- `SHOW ENGINE STATS`, `SHOW SLOW QUERIES`, `SHOW VARIABLES`, and the
  dashboard are node-local. Query every node.
- Engine counters and histograms never reset. One reading tells you nothing
  about a rate.
- The slow query log is off by default, in memory only, and lost on restart.
- An idle explicit transaction is not in `SHOW QUERIES`. Find its connection in
  `SHOW CONNECTIONS` by a large `idle_ms`.
- Only reads are cancellable. There is no `KILL CONNECTION`.
- `LIKE` in these statements is case-sensitive. Names are lowercase.
- The operator dashboard is on the node's HTTP port. It is read-only and keeps
  no history.
- For history and alerts, enable the `diagnostics:` section (Prometheus at
  `/metrics`, optional OTLP traces). It serves a standalone node.

Read [references/observability.md](references/observability.md) for every
column, metric name, setting, and permission.

## Query plans, briefly

When the slow query log shows `full_scan = true`, or `rows_read` far above
`rows_returned`, the plan needs work. Run `EXPLAIN` or `EXPLAIN ANALYZE` on the
statement. When it shows `spilled = true`, add a `LIMIT` or review memory and
the spill threshold. Hand the plan analysis to the `camusdb-query-performance`
skill.

## Common mistakes

- **Replaying after `CADB0509`.** It is not like a PostgreSQL serialization
  failure. The commit may have applied. Resend the same finalize.
- **Retrying `CADB0506`.** Like a Spanner mutation-limit error, it is
  permanent for that transaction shape. Split the work. CamusDB counts the row,
  the primary-key entry, each secondary index entry, and each out-of-line value.
- **Looking for `pg_stat_activity` or `SHOW PROCESSLIST`.** Use `SHOW QUERIES`
  and `SHOW CONNECTIONS`.
- **`pg_cancel_backend`, `KILL QUERY`, or `KILL CONNECTION`.** Use
  `CANCEL QUERY '<query_id>'`. It stops reads only.
- **`SHOW GLOBAL STATUS` or `SET GLOBAL`.** Use `SHOW ENGINE STATS` for metrics.
  Use `SET CLUSTER SETTING` and `RESET CLUSTER SETTING` for changes.
  `SHOW VARIABLES` is read-only.
- **Reading `config.yml` to learn the running config.** Env vars, CLI flags,
  and cluster settings override it. `SHOW VARIABLES` shows the effective value
  and its `source`.
- **Expecting cluster-wide answers from one node.** Only `SHOW CLUSTER QUERIES`
  and `SHOW CLUSTER CONNECTIONS` span the cluster. Other diagnostics do not.
- **Adding a pooler such as PgBouncer for "too many connections".** CamusDB has
  no per-connection process. Use the admission gate to bound work.
- **Expecting long lock waits or a lock-timeout error.** One lock wait is
  capped by `lock_wait_deadline_ms` (default 500 ms). CamusDB then reports a
  conflict, usually `CADB0502`.
- **Double-quoted identifiers.** In CamusDB, double quotes make a string
  literal. Use backticks for identifiers. A wrong quote can give wrong results
  with no error.
- **Expecting a "snapshot too old" error.** An `AS OF SYSTEM TIME` read past
  retained history returns an empty result. Only a read before a `TRUNCATE`
  cut errors, with `CADB0537`.

## References

Read these files when the task needs the detail:

- [references/error-codes.md](references/error-codes.md): every code with name,
  meaning, fix, and retry class. Read it for any `CADBxxxx` code.
- [references/symptom-playbooks.md](references/symptom-playbooks.md): ordered
  checks and fixes for 13 symptoms.
- [references/observability.md](references/observability.md): columns,
  metrics, settings, and permissions of each tool.

Public docs:

- https://camusdb.github.io/docs/error-codes
- https://camusdb.github.io/docs/performance-diagnostics
- https://camusdb.github.io/docs/engine-stats
- https://camusdb.github.io/docs/query-activity
- https://camusdb.github.io/docs/slow-query-log
- https://camusdb.github.io/docs/show-variables
- https://camusdb.github.io/docs/operator-dashboard
- https://camusdb.github.io/docs/connection-model
- https://camusdb.github.io/docs/wal-recovery
- https://camusdb.github.io/docs/serializable-retries
- https://camusdb.github.io/docs/transaction-limits
