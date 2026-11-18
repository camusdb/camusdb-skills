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
14. Statement refused with `CADB0824` after an earlier error
15. `UPDATE` or `DELETE` now fails with `CADB0400` on a type mismatch
16. `SUM` or arithmetic now fails with `CADB0420`
17. Result too large (`CADB0556`)
18. Too many open transactions (`CADB0558`)
19. Node refuses to start with `CADB0600` routing mode
20. A statement fails with `CADB0400` after an upgrade
21. Syntax error at an invisible character (`CADB0406`)
22. Detected storage damage (`CADB0837`)
23. Historical or snapshot read fails with `CADB0847`
24. Recursive query fails with `CADB0560`

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
7. A commit refused by the coordinator names its refusal class in the message.
8. `SHOW STATEMENT STATS` (if enabled): the `retries` column names the shapes that
   fail with `CADB0502`, `CADB0504`, or `CADB0505`.

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
| The message names the budget that ran out and the key counts | `replication_wait_deadline_ms` (default `5000`) means a key still replicating or settling, not a held lock. |
| A read fails after about 5 s (scan page) or 16 s (point read) | The read waited for a commit in progress. |

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
- After a rollback by the reaper, the next statement gets `CADB0400` "Unknown
  transaction".
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

- `CANCEL QUERY '<query_id>'` for a read. The client gets `CADB0554`. Inside an
  explicit transaction, the cancel aborts the transaction.
- Tune the plan. Use the `camusdb-query-performance` skill.
- Fix a slow consumer, or use a buffered endpoint.

## 5. Queries were slow earlier

Checks:

1. `SHOW VARIABLES LIKE 'slow_query%'`. If `slow_query_log_enabled` is `false`,
   run `SET CLUSTER SETTING slow_query_log_enabled = true`. The next statement is
   recorded. Nothing earlier is available.
2. `SHOW SLOW QUERIES` on each node.
3. Read the flags:

| Pattern | Meaning | Next step |
| --- | --- | --- |
| `rows_read` far above `rows_returned` | Predicate lacks a selective index | Add or fix an index |
| `full_scan = true` | A relation was read whole, maybe a join side | Index the join or filter column |
| `spilled = true`, `rows_read` near `rows_returned` | Memory: large sort, group, or hash | Add `LIMIT`, raise the spill threshold, check `EXPLAIN` |
| `outcome = failed` | Statement failed | Look up `error_code` |
| `outcome = abandoned` | Client went away | Check client timeouts |

The log misses a statement that is fast each time but runs often. Use
`SHOW STATEMENT STATS` for that: look at `total_ms`, and compare `rows_read` with
`rows_returned`. A high `cv` or a rising `plan_changes` points to a plan change.

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
   `kahuna.kv.write.*`, `raft.wal.*`, `kahuna.backup.*`, `kahuna.collect.*`.
4. Compare `max` with `total / count`. A high `max` with normal `last` means a
   periodic stall.
5. `SHOW ENGINE STATS LIKE 'kahuna.collect%'`. A `kahuna.collect.cycle.duration`
   spike that lines up with the latency spike points to a Kahuna collection
   cycle. `backlogged` close to `cycles` means collection cannot keep up.
   `inspected` far above `evicted` means the cycles spend their budget on pinned
   entries.

Fixes depend on the cycle found: backup schedule, WAL flush pressure, write
batch behavior, or collection cycles.

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
- A partition that cannot reach a majority of its members stops all commits.
  It does not accept a change that could conflict with another copy. Restore
  enough healthy members, then retry.

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
- A historical read never returns empty for history that retention removed. It
  fails with `CADB0847`. See playbook 23.
- Under `READ COMMITTED`, a second statement can see different rows and phantoms.
  One `SELECT` reads one snapshot in a pessimistic transaction.
- A read at a snapshot can miss a commit from a node with a later clock. The .NET
  and TypeScript gRPC clients carry the causal token for you. Python does not
  track causal tokens. A custom client passes the token of that commit.
- After an upgrade, a stored `LIKE` pattern in a view uses the new rules: `_`
  matches one character and a backslash escapes. Escape literal underscores.
- An unaliased expression column is named by its zero-based position in the
  select list, not by the function name. Alias computed columns.
- `LIKE` in `SHOW` statements is case-sensitive. `LIKE 'TTL_%'` matches nothing.

## 14. Statement refused with `CADB0824` after an earlier error

Any failed statement in an explicit transaction aborts it. CamusDB has no
savepoints. Each later statement, reads included, gets `CADB0824`. `COMMIT` rolls
back and reports `CADB0824`. Nothing is stored. A cancelled statement and a
statement refused in a transaction block (`CADB0538`) abort it too.

Over HTTP and gRPC, the server rolls back as soon as the statement fails. The next
statement or `COMMIT` then gets `CADB0400` "Unknown transaction". A `ROLLBACK` still
succeeds.

Checks:

1. Read the `CADB0824` message. It names the statement that failed and its code.
2. Look in the client for a `catch` that swallows an error and continues in the
   same transaction. A caught duplicate key (`CADB0300`) is the common case.
3. Find the first error of that transaction in the client log.

Fixes:

- Roll back and start a new transaction. Replay it if the first error is retryable.
- Replace the caught duplicate key with `INSERT ... ON CONFLICT` or `UPSERT INTO`.
- See the `camusdb-transactions` skill for the driver retry helpers.

## 15. `UPDATE` or `DELETE` now fails with `CADB0400` on a type mismatch

Two types with no comparison rule now fail with `CADB0400`. Before, the comparison
was `FALSE`, so the statement reported success and matched nothing.

```text
No matching signature for operator = for argument types: Integer64, String
```

- Pairs with no rule include `INT64` with `STRING`, `FLOAT64` with `STRING`, `BOOL`
  with `INT64`, `BYTES` with `STRING`, and `NUMERIC` with `STRING`.
- The rule covers `IN`, `BETWEEN`, simple `CASE`, `JOIN ... ON`, `HAVING`, and typed
  parameters. `1 IN (1, 'a')` fails even though an item matches.
- Known incompatible types fail before any row is read, even on an empty table.
  Types known only from a parameter's value are checked when the value compares.
- A `STRING` against `UUID`, `ID`, or `JSONB` that does not parse is still a non-match.
- `CASE`, `COALESCE`, `IFNULL`, and `IF` need one result type. Other pairs give
  `CADB0400` when the statement is bound.

Fixes:

- Bind the parameter with the column's type. The old statement never matched, so
  check whether earlier runs did the intended work.
- Compare on purpose with a cast: `CAST(s AS INT64) = a`.

## 16. `SUM` or arithmetic now fails with `CADB0420`

`INT64` never wraps. A result outside its range fails with `CADB0420`, and the
statement stores nothing. `CADB0421` is the same rule for floats.

- Checked: `+`, `-`, `*`, `/`, unary minus, integer literals, conversions to
  `INT64`, and the `SUM` total. Only the final `SUM` total is checked.
- A new `SMALLINT` column accepts -32768 to 32767. A new `INT` or `INTEGER` column
  accepts the signed 32-bit range. A write, cast, default, or sequence value
  outside it fails with `CADB0420`. Columns created before this rule keep the
  full `INT64` range.
- A float cast to an integer rounds half away from zero, then checks the range.
  NaN and infinity fail with `CADB0420`.
- `exp` overflow, or underflow to zero, fails with `CADB0421`.
- `AVG` of `INT64` divides the exact total and never overflows.

Fixes:

- Use `NUMERIC` for a wider exact value: `SUM(CAST(x AS NUMERIC))`.
- Declare `BIGINT` or `INT64` for a column whose values can pass 32 bits.
- Find bad values if the total should fit. Inside an explicit transaction, the
  failure aborts the transaction.

## 17. Result too large (`CADB0556`)

- `/execute-sql-query`, `/query`, and a write with `RETURNING` hold the whole result
  in memory. Past `max_buffered_result_rows` (default `1000000`) or
  `max_buffered_result_bytes` (default 256 MiB), the node stops the read. The
  statement rolls back, including its writes.
- The byte estimate counts two bytes per character, so it exceeds the JSON size.

Fixes:

- Use `/execute-sql-query-stream` or the gRPC query stream. They are not capped.
- Page with `LIMIT` and `OFFSET`.
- Raise a cap with `SET CLUSTER SETTING`. A value of `0` or less disables it. The
  cap protects the node from running out of memory.

## 18. Too many open transactions (`CADB0558`)

The code has two causes. Read the message:

- A `BEGIN` passed `max_open_explicit_transactions` (default `10000`, per node).
  Autocommit is never refused.
- A `SHOW CLUSTER QUERIES` or `SHOW CLUSTER CONNECTIONS` passed
  `cluster_activity_max_concurrent_fan_outs` (default `8`).

Nothing ran, so retry after a back-off.

Checks:

1. `SHOW CONNECTIONS`: look for `active_queries = 0` with a large `idle_ms`.
2. Check the client: every code path must commit or roll back.
3. `SHOW VARIABLES LIKE 'transaction_%'`: a shorter idle timeout reclaims leaks.

## 19. Node refuses to start with `CADB0600` routing mode

A cluster records its routing mode once, at creation, in `_system/routing/mode`. A
node whose `key_range_sharding` or `CAMUS_KEY_RANGE_SHARDING` names the other mode
refuses to start and exits with code 1. The message names both modes.

Fixes:

- Remove the setting, so the node follows the record. `SHOW VARIABLES` then reports
  `source = cluster`.
- Or set it to the recorded mode.
- To move data to the other mode, dump it and reimport it into a new cluster.

Docs:

- https://camusdb.github.io/docs/query-activity
- https://camusdb.github.io/docs/slow-query-log
- https://camusdb.github.io/docs/statement-stats
- https://camusdb.github.io/docs/engine-stats
- https://camusdb.github.io/docs/performance-diagnostics
- https://camusdb.github.io/docs/sql-transactions#failed-statements
- https://camusdb.github.io/docs/settings-reference
- https://camusdb.github.io/docs/logical-dump-and-reimport
- https://camusdb.github.io/docs/transaction-priority
- https://camusdb.github.io/docs/spill-to-disk
- https://camusdb.github.io/docs/distributed-schema
- https://camusdb.github.io/docs/wal-recovery
- https://camusdb.github.io/docs/connection-model
- https://camusdb.github.io/docs/cluster
- https://camusdb.github.io/docs/time-travel-reads
- https://camusdb.github.io/docs/sql-queries#recursive-ctes

## 20. A statement fails with `CADB0400` after an upgrade

Newer builds check more mistakes before they read a row. The statement fails
even on an empty table, under `LIMIT 0` or `WHERE FALSE`, or in an unused
branch. Read the message, then match it:

| Cause | Example | Fix |
| --- | --- | --- |
| A condition that is not boolean | `WHERE 1`, `WHERE 'true'` | `WHERE TRUE`, or a real comparison. |
| A constant division by zero | `SELECT 1 / 0 WHERE FALSE` | Remove it. Guard a row divisor: `CASE WHEN q <> 0 THEN 1 / q END`. |
| A negative `LIMIT` or `OFFSET` | `LIMIT -1`, or a negative `@limit` | Validate the value in the application. |
| An ambiguous `ORDER BY` name | Two different output columns named `total` | Give unique aliases, or `ORDER BY 2`. |
| A bare input column in a global aggregate's `ORDER BY` | `SELECT COUNT(*) FROM t ORDER BY id` | Order by an output alias or position, or remove it. |
| Half of a surrogate pair in a string parameter | An emoji cut in half by the client | Fix the text at the application boundary. The message names the parameter and position. |
| Malformed JSON body on a SQL endpoint | An escaped lone surrogate | Fix the client encoder. The message gives the JSON path and byte position. |
| A bad `LIKE` escape | `ESCAPE 'ab'`, or a pattern that ends with its escape character | Use a one-character `ESCAPE`. Escape or remove the trailing character. |
| A wrong argument count | `substring(s)` | Fix the call. An unknown function gives `CADB0407`. |

These are not retryable. A gRPC client can replace a surrogate half with U+FFFD
before it sends, and the server cannot detect that change. Validate text before
you encode it.

`COALESCE`, `IFNULL`, and `NVL` now stop at the first value that is not NULL:
`COALESCE(1, 1 / 0)` returns 1. A proven unused branch is skipped, but a
row-dependent condition cannot hide a constant division error. A known type
mismatch still fails: `COALESCE(1, upper('a'))` is an error.

## 21. Syntax error at an invisible character (`CADB0406`)

The parser refuses an unknown character outside a literal, quoted identifier,
or comment. The message gives the line and column.

Checks:

1. Go to the line and column in the message.
2. Look for a non-breaking space (U+00A0), a byte-order mark (U+FEFF), a stray
   backslash, or a bare `$`. SQL copied from a web page or a document often has
   them.
3. Look for a raw line break or tab inside a string literal or quoted identifier.
4. Look for a second statement in the same request. A request holds one
   statement. Repeated trailing semicolons are allowed.

Fixes:

- Retype the whitespace as plain spaces.
- Use an escape string for a line break: `E'line\nnext'`.
- Split a script into one request per statement. A client that runs scripts
  sends each statement as its own request.
- Replace a comparison chain `a = b = c` with `a = b AND b = c`.

## 22. Detected storage damage (`CADB0837`)

A RocksDB or Solarium store that detects damage stops serving data. The node
gives up leadership and does not campaign.

Checks:

1. `GET /v1/cluster/health` on each node. A storage fault makes `ready` false
   and returns HTTP 503. Read `storageFault` and `partitionStorageFaults` in the
   body. With authentication on, only a superuser sees these details.
2. Check the log for a Critical message about lost acknowledged log entries. By
   default (`kahuna.raft_lost_acknowledged_entries_policy: quarantine`) such a
   replica does not vote or campaign until its leader repairs it.

Fixes:

- Never treat `CADB0837` as an empty result, and do not retry it as a conflict.
- Keep the node out of service. Let the surviving replicas serve the data, or
  restore a backup. See the `camusdb-migrations-and-recovery` skill.
- The endpoint does not scan for damage or repair it. SQLite has no page
  checksums, so detection there is weaker.

## 23. Historical or snapshot read fails with `CADB0847`

`CADB0847 SnapshotTooOld` (HTTP 409) means retention removed history that a
snapshot read needs. CamusDB fails rather than return missing rows or older
values. Two reads can get it: an `AS OF SYSTEM TIME` read, and a long snapshot
transaction.

Checks:

1. Compare the read timestamp with the retention window.
2. `SHOW VARIABLES LIKE 'kahuna.persistent\_revision%'`.
   `kahuna.persistent_revision_retention_age_seconds` keeps old revisions for
   that age. With neither retention key set, it follows
   `kahuna.pitr_window_seconds` (3,600 by default). `0` turns age pruning off.
   `kahuna.persistent_revision_retention_count` caps revisions per key. Its
   default, `0`, turns count pruning off.
3. A read before a `TRUNCATE` cut gives `CADB0537` instead. That is a
   different fix: `CREATE TABLE ... RELINK TO`.

Fixes:

- Do not retry at the same timestamp. Pruned history does not come back.
- Choose a newer `AS OF SYSTEM TIME` timestamp.
- For a long snapshot transaction, roll back and begin a new transaction.
- Raise retention before running work that needs older history. Set it under
  `kahuna:` in `config.yml` and restart. `SET CLUSTER SETTING` rejects every
  `kahuna.*` key. A nonzero age must be at least `kahuna.pitr_window_seconds`.
- A branch protects its fork snapshot with holds, apart from the age window.

## 24. Recursive query fails with `CADB0560`

`CADB0560 RecursiveQueryLimitExceeded` (HTTP 400) means a `WITH RECURSIVE`
query passed one of two limits. A retry of the same query hits the same limit.

| Setting | Default | Counts |
| --- | --- | --- |
| `max_recursive_cte_iterations` | `10000` | Recursive steps that produce rows |
| `max_recursive_cte_rows` | `10000000` | Total rows per run, seed rows included |

Checks:

1. Look for a stopping predicate in the recursive term, such as a depth bound.
   Recursion ends only when a step produces no rows. On a cyclic graph,
   `UNION ALL` keeps repeating rows. `UNION` drops repeated rows, but a changing
   depth or path column keeps them distinct.
2. Count the readers of the CTE. A single reader can stream rows, so its `LIMIT`
   can stop recursion early. With several readers, CamusDB computes the whole
   result first, and an outer `LIMIT` does not stop recursion.

Fixes:

- Add a terminating predicate, such as `WHERE depth < 20`.
- Raise a limit with `SET CLUSTER SETTING` only after you check the query. Both
  are runtime cluster settings. `0` disables a limit.
- A deep chain of CTEs that reuse each other can fail with `CADB0413` instead.
  See the `CADB0413` row in `error-codes.md`.
