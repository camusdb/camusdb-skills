# Symptom playbooks

Read this file when the user reports a symptom instead of, or in addition to, an
error code. Each playbook lists checks in order, then fixes.

## Contents

1. Conflict storm (`CADB0502`)
2. Transient retry spikes (`CADB0504`)
3. A transaction seems stuck or holds locks
4. One query is slow right now
5. Queries were slow earlier
6. Periodic latency spikes or "Slow dispatch" warnings
7. Node overload
8. Writes refused for disk space (`CADB0536`)
9. Spill failures (`CADB0507`, `CADB0549`)
10. A node rejects reads and DML after DDL (`CADB0503`)
11. Errors after a restart or leader change
12. Nodes behave differently
13. Wrong results with no error

## 1. Conflict storm (`CADB0502`)

Checks:

1. Read the error message. It samples the contended keys and names the table.
2. Look for one row that every transaction updates: a counter, a balance, a
   configuration row.
3. `SHOW QUERIES` for long-running writers on the same table.
4. Check transaction length in the application code.
5. Check for wide serializable scans before a write. Range locks block writers.
6. Check for a parent row that many child inserts reference. Each child write
   takes a shared lock on the parent key.

Fixes:

- Shard the hot row. Spread writes across N shard rows. Sum on read.
- Shorten transactions. Move reads that do not feed a write into a
  `SERIALIZABLE READ ONLY` transaction or an `AS OF SYSTEM TIME` read.
- Express read-modify-write as one `UPDATE ... SET x = x + 1`.
- Make sure the client replays from `BEGIN` with jittered backoff.
- See the `camusdb-transactions` skill for retry code.

## 2. Transient retry spikes (`CADB0504`)

`CADB0504` has several causes. Find which one:

| Check | Points to |
| --- | --- |
| `SHOW ENGINE STATS LIKE 'kahuna.tx_admission%'` shows `queued` or `rejected_queue_full` rising | Admission gate saturation. `kahuna.max_concurrent_sessions` is too low, or no reserve. |
| `raft.elections_started_total` rising between two readings | Leadership churn. |
| `SHOW ENGINE STATS LIKE 'kahuna.range%'` shows splits or moves | Key-range sharding moved a range during the write. |
| Lock waits over `lock_wait_deadline_ms` (default `500`) | Contention, as in playbook 1. |

Fixes:

- Retry from `BEGIN` with backoff. Nothing was written.
- Set the gate ceiling at or above healthy concurrency. Set
  `transaction_priority_reserved_slots` to 1 or more.
- Keep `kahuna.default_admission_wait_ms` short (seconds). A long wait only
  makes refusals slower.
- Investigate the cause of elections (CPU, network, disk).

## 3. A transaction seems stuck or holds locks

Checks:

1. `SHOW QUERIES`. An active statement shows `elapsed_ms` and `phase`.
2. An idle explicit transaction does not appear in `SHOW QUERIES`. Find its
   connection in `SHOW CONNECTIONS`: `active_queries = 0` and a large `idle_ms`.
3. `SHOW VARIABLES LIKE 'transaction_%'` for the idle timeout and reaper
   interval.

Facts:

- CamusDB rolls back an idle explicit transaction after
  `transaction_idle_timeout_ms` (default `300000`, 5 minutes). The reaper runs
  every `transaction_reaper_interval_ms` (default `30000`).
- A serializable read-write transaction aborts after
  `max_serializable_transaction_lifetime_ms` (default 1 hour).
- A `BatchExecute` stream transaction rolls back when the stream ends.
- There is no `KILL CONNECTION`. `CANCEL QUERY` stops reads only.

Fixes:

- Fix the client so it always commits or rolls back, including on errors.
- Never wait for user input inside a transaction.
- Lower `transaction_idle_timeout_ms` if abandoned transactions are common.

## 4. One query is slow right now

Checks:

1. `SHOW QUERIES LIKE '%<fragment>%'`. Note `phase`, `elapsed_ms`,
   `rows_returned`.
2. `phase = planning` for long: the statement has not read its first row yet.
3. `rows_returned` growing slowly: the client reads slowly. The statement stays
   listed until the cursor ends.
4. Run `EXPLAIN` on the statement. Look for full scans.

Fixes:

- `CANCEL QUERY '<query_id>'` for a read. The client gets `CADB0554`.
- Tune the plan. Use the `camusdb-query-performance` skill.
- Fix a slow consumer, or use a buffered endpoint.

## 5. Queries were slow earlier

Checks:

1. `SHOW VARIABLES LIKE 'slow_query%'`. If `slow_query_log_enabled` is `false`,
   enable it in `config.yml`. History before that is lost.
2. `SHOW SLOW QUERIES` on each node.
3. Read the flags:

| Pattern | Meaning | Next step |
| --- | --- | --- |
| `rows_read` far above `rows_returned` | Predicate lacks a selective index | Add or fix an index |
| `full_scan = true` | A relation was read whole, maybe a join side | Index the join or filter column |
| `spilled = true`, `rows_read` near `rows_returned` | Memory: large sort, group, or hash | Add `LIMIT`, raise the spill threshold, check `EXPLAIN` |
| `outcome = failed` | Statement failed | Look up `error_code` |
| `outcome = abandoned` | Client went away | Check client timeouts |

## 6. Periodic latency spikes or "Slow dispatch" warnings

Log line example:

```text
warn: Kommander.IRaft[0] [RaftPartitionExecutor/1] Slow dispatch: CheckLeader took 468ms
```

Checks:

1. `SHOW ENGINE STATS LIKE 'raft.executor%';` now and again one minute later.
2. If `operation_duration_ms.max` jumped while `operations_total` grew a little,
   the stall did not come from load.
3. Widen the filter. Find what advanced in the same interval:
   `kahuna.kv.write.*`, `raft.wal.*`, `kahuna.backup.*`.
4. Compare `max` with `total / count`. A high `max` with normal `last` means a
   periodic stall.

Fixes depend on the cycle found: backup schedule, WAL flush pressure, or write
batch behavior. A collection tick has no metric yet in the current Kahuna
version; it appears as an unexplained gap.

## 7. Node overload

Signals:

- `raft.executor.rejections_total` above zero over time: partition queues full.
- `raft.heartbeat_delay_ms` well above the heartbeat interval: CPU shortage.
- `raft.wal.batch_size` mean near 1: no WAL coalescing.
- `kahuna.kv.write.entries / kahuna.kv.write.batches` low: little write
  coalescing.
- `camus_request_in_flight` high in Prometheus.
- Dashboard Load panel: in-flight requests and open transactions.

Fixes:

- Add admission control with `kahuna.max_concurrent_sessions` and a reserve.
  Connections are cheap; work is not.
- Mark bulk jobs `SET TRANSACTION PRIORITY BACKGROUND` once the gate is on.
- Rate-limit background analyze with `auto_analyze_max_rows_per_second`.
- Use the snapshot script `scripts/bottleneck-snapshot.sh` from the source tree
  for a full bottleneck report.

## 8. Writes refused for disk space (`CADB0536`)

- Writes fail before mutation when free space on the data volume is below
  `min_free_disk_bytes` (default `67108864`, 64 MiB). HTTP 507.
- Reads, DDL, and internal work keep working, so you can recover space.
- Fix: free disk on that node, or lower the threshold. Then retry.
- Spill files share the volume. Check for large spilling queries too.

## 9. Spill failures (`CADB0507`, `CADB0549`)

| Code | Cause | Fix |
| --- | --- | --- |
| `CADB0507` | Cannot create the spill directory or open a spill file | Free disk, fix permissions under `data_dir`, or run on another node |
| `CADB0549` | Live spill files would exceed `spill_max_total_bytes` (default `8589934592`) | Let other spilling queries finish, raise the limit, or reduce the spill with a `LIMIT` or an index |
| `CADB0536` during spill | Spill would cross `min_free_disk_bytes` | Free disk |

The failed statement keeps no partial write. A spilling `UPDATE` or `DELETE`
changes no row.

## 10. A node rejects reads and DML after DDL (`CADB0503`)

- The node fell more than one schema version behind the committed schema head.
- It rejects reads and DML for that database until it catches up.
- Fix: retry later, or send the work to another node. This is temporary, not a
  stale-read mode.
- In a cluster, a node can miss a schema delta. CamusDB reloads the schema
  before it returns `CADB0011 TableDoesntExist`, so that error reflects the
  durable schema, not a stale cache.

## 11. Errors after a restart or leader change

- At startup, each partition restores from its WAL. The node serves no normal
  work for that partition before the restore finishes.
- A transaction that never committed does not reappear. Uncommitted work is
  lost by design.
- A commit acknowledged before a crash is replayed from the WAL.
- During an election you may see `CADB0504`, `CADB0509`, or `CADB0535`
  (`SequenceUnavailable`). Retry each per its rule. `CADB0509` needs the same
  finalize, not a replay.
- `CADB0520` after a restart: prepared handles are gone. Prepare again.
- An explicit transaction lives on its node. If that node restarted, the
  transaction is gone. Replay from `BEGIN`.

## 12. Nodes behave differently

- Run `SHOW VARIABLES` on each node. Compare line by line; the output is sorted.
- Check `source`: `cluster`, `cli`, `env`, `config`, `default`.
- Check `mutability = restart` settings changed without a restart.
- `SHOW CLUSTER SETTINGS` (dashboard Overlay panel) lists runtime overrides.
- A settings change that breaks an invariant fails with `CADB0600` and applies
  nothing.

## 13. Wrong results with no error

- A double-quoted token is a string literal in CamusDB, not an identifier.
  `WHERE "status" = 'x'` compares two strings. Use backticks for identifiers.
- An expired TTL row stays readable until the sweep deletes it. Filter with
  `WHERE expires_at > NOW()`.
- An `AS OF SYSTEM TIME` read older than retained history returns empty, not an
  error.
- Under `READ COMMITTED`, a range scan is non-repeatable and allows phantoms.
- `LIKE` in `SHOW` statements is case-sensitive. `LIKE 'TTL_%'` matches nothing.

Docs:

- https://camusdb.github.io/docs/query-activity
- https://camusdb.github.io/docs/slow-query-log
- https://camusdb.github.io/docs/engine-stats
- https://camusdb.github.io/docs/performance-diagnostics
- https://camusdb.github.io/docs/transaction-priority
- https://camusdb.github.io/docs/spill-to-disk
- https://camusdb.github.io/docs/distributed-schema
- https://camusdb.github.io/docs/wal-recovery
- https://camusdb.github.io/docs/connection-model
