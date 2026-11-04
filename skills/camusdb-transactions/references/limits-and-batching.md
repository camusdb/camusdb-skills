# Limits, batching, and contention

Read this file when a transaction is large, long, or contended. It covers the
limits, the mutation math, batching patterns, hot rows, and the priority gate.

## Contents

- Limits
- What counts as a mutation
- Batching an UPDATE or DELETE
- Emptying or expiring rows
- Hot rows
- Priority and the admission gate
- Idle and abandoned transactions

## Limits

| Limit | Default | Setting | Failure |
| --- | --- | --- | --- |
| Serializable read-write lifetime | 1 hour (`3600000` ms) | `max_serializable_transaction_lifetime_ms` | `CADB0505`, replay from `BEGIN` |
| Mutations per user DML transaction | 20,000 | `max_mutations_per_transaction` | `CADB0506`, split the work |
| Idle time inside an explicit transaction | 5 minutes (`300000` ms) | `transaction_idle_timeout_ms` | CamusDB rolls it back. The next statement gets `CADB0400` "Unknown transaction". |
| Reaper interval | 30 seconds (`30000` ms) | `transaction_reaper_interval_ms` | How often CamusDB looks for idle transactions |
| Concurrent coordinator sessions per node | `0`, no gate | `kahuna.max_concurrent_sessions` | Queued, then `CADB0504` |
| Open explicit transactions per node | `10000` | `max_open_explicit_transactions` | `CADB0558` on `BEGIN`, retry after a back-off. Autocommit is not counted. |
| Rows in one buffered response | `1000000` | `max_buffered_result_rows` | `CADB0556`, the statement rolls back |
| Estimated bytes in one buffered response | `268435456` (256 MiB) | `max_buffered_result_bytes` | `CADB0556`, the statement rolls back |

Notes:

- CamusDB checks the lifetime at range-lock acquisition and at commit.
- CamusDB rejects a `CADB0506` statement before its writes reach storage.
- A value `<= 0` for `max_mutations_per_transaction` disables the cap on user
  DML. Do this only on purpose.
- Schema DDL and internal backfill run without the mutation cap. User DML is
  always counted.
- A `CADB0556` also rolls back a write with `RETURNING`. Use a streaming transport,
  or page the result. Inside an explicit transaction it aborts the transaction.

## What counts as a mutation

One mutation is one write or delete of a row blob, or of one primary-key or
secondary index entry. An out-of-line large value (`STRING`, `BYTES`/`BLOB`, or
`ARRAY` stored outside the row) is one more mutation.

| Operation | Mutations |
| --- | --- |
| Insert one row, `K` secondary indexes, `L` out-of-line values | `2 + K + L` |
| Delete one row, `K` secondary indexes, `L` out-of-line values | `2 + K + L` |
| Update one row, changing `M` indexed columns | `1 + 2M` |

An update that does not change a large value costs nothing extra for that value.
The counter only rises. Two updates of the same row count twice.

Each row that `INSERT ... ON CONFLICT` inserts or updates counts. A skipped row
counts nothing, and the locks are not mutations.

Worked examples with the default cap of 20,000:

| Table shape | Cost per insert | Rows per transaction |
| --- | --- | --- |
| No secondary index | 2 | 10,000 |
| Two secondary indexes | 4 | 5,000 |
| No secondary index, four out-of-line values | 6 | 3,333 |

Each foreign key may add an owned `~fk_` index on the child. That index is one
more mutation per child insert.

## Batching an UPDATE or DELETE

Split by a key range that your application controls:

```sql
UPDATE orders
SET status = 'shipped'
WHERE shipped_at IS NOT NULL
  AND id >= @start_id
  AND id < @end_id;
```

Procedure:

1. Compute the per-row cost from the table above.
2. Pick a batch size well below `20000 / cost`. Leave room for index changes.
3. Run one autocommit statement, or one short transaction, per range.
4. Advance the range. Repeat until no rows remain.
5. Make each batch idempotent. The predicate must not match a row twice in a
   way that changes the result.
6. Back off and retry a batch on `CADB0502`, `CADB0504`, or `CADB0505`.
7. On `CADB0506`, halve the batch size. Do not resend the same batch.

Optional: mark bulk work as deferrable:

```sql
BEGIN;
SET TRANSACTION PRIORITY BACKGROUND;
DELETE FROM events WHERE created_at < '2026-01-01' AND id >= @start_id AND id < @end_id;
COMMIT;
```

A large single `DELETE` also holds locks for a long time. Batching shortens the
lock hold, not only the mutation count.

## Emptying or expiring rows

- `TRUNCATE` replaces the key space of a table. It counts no mutation. Run it in
  autocommit. Inside `BEGIN` it fails with `CADB0538`.
- After `TRUNCATE`, an `AS OF SYSTEM TIME` read before the cut fails with
  `CADB0537`.
- Row-level TTL deletes expired rows in small background batches:

```sql
CREATE TABLE sessions (
  id UUID PRIMARY KEY NOT NULL DEFAULT (gen_uuid_v4()),
  player_id UUID NOT NULL,
  expires_at DATETIME NOT NULL
) WITH (ttl_expiration_expression = 'expires_at');
```

TTL facts:

- An expired row stays readable until the sweep deletes it. Filter with
  `WHERE expires_at > NOW()` when you need an exact expiry.
- A TTL table cannot be the parent of a foreign key.
- Each deleted row costs the usual row and index mutations.

## Hot rows

A global counter or a single configuration row that every transaction updates
makes all writers conflict on one key. Serializable transactions then abort with
`CADB0502`, and every client replays.

Shard the counter:

```sql
CREATE TABLE stat_counters (
  name STRING(64) NOT NULL,
  shard INT64 NOT NULL,
  value INT64 NOT NULL
) PRIMARY KEY (name, shard);

UPDATE stat_counters
SET value = value + 1
WHERE name = 'matches_played' AND shard = @shard;

SELECT SUM(value) AS total
FROM stat_counters
WHERE name = 'matches_played';
```

Choose `@shard` at random in the application. Seed one row per shard first.

Alternatives:

- Compute the total later from the source rows.
- Refresh a materialized view on a schedule.

For a counter with few writers, an upsert creates the row and adds in one
statement. Writers of the same new key can still get a retryable conflict. See
`locking-reads-and-upserts.md`.

Other contention sources:

- A sequence is one logical counter. Every insert that needs a value coordinates
  through it.
- A parent row that many children reference, and that also changes often. Each
  child write takes a shared lock on the parent key.
- A wide serializable scan or join before a write. Its range lock blocks writers
  into the range. A Read Committed scan takes no range lock.

## Priority and the admission gate

Priority orders which queued transaction starts next on a saturated node. It
does nothing until an operator sets `kahuna.max_concurrent_sessions`.

| Priority | Use for |
| --- | --- |
| `BACKGROUND` | Bulk work you can defer. Yields to everything. |
| `LOW` | Below ordinary traffic, still latency-relevant. |
| `NORMAL` | The default. |
| `HIGH` | Latency-critical work. |
| `CRITICAL` | Work that must not be deferred. Can use reserved slots. Use rarely. |

What priority does not do:

- It does not preempt, throttle, or deschedule an admitted transaction.
- It does not affect a lock conflict.
- It applies per node. There is no cluster-wide fairness.

Gate settings, under `kahuna:` in `config.yml`:

```yaml
kahuna:
  max_concurrent_sessions: 64             # 0 (default) = no gate
  transaction_priority_reserved_slots: 2  # slots only High/Critical may use
  transaction_priority_aging_threshold: 1000
  transaction_priority_max_queued: 4096
  default_admission_wait_ms: 5000
  max_admission_wait_ms: 30000
```

- The ceiling bounds all engine work on the node, including internal catalog
  writes and backfill. Set it at or above healthy concurrency.
- Set `transaction_priority_reserved_slots` to 1 or more whenever you set a
  ceiling.
- Aging raises a waiter one level per threshold. With the default 1,000 ms, a
  `BACKGROUND` transaction reaches `HIGH` after about three seconds.
- A transaction that waits past the admission budget fails with `CADB0504`.
  Nothing started, so a retry is safe. Use backoff.
- Watch `kahuna.tx_admission.queued`. Zero means the gate does not bind.

A read-committed autocommit `SELECT` holds no session. The gate never defers it.

To make background work cheaper, use a rate limit, not priority. Examples are
`auto_analyze_max_rows_per_second` and `auto_analyze_load_pause_threshold`.

## Idle and abandoned transactions

- An explicit transaction holds its locks until it ends.
- CamusDB rolls it back after `transaction_idle_timeout_ms` with no statement.
- The Kahuna session timeout, equal to the lifetime cap, is the final backstop.
- An idle transaction does not appear in `SHOW QUERIES`. Its connection appears
  in `SHOW CONNECTIONS`.
- Never wait for user input inside a transaction.

Docs:

- https://camusdb.github.io/docs/transaction-limits
- https://camusdb.github.io/docs/settings-reference
- https://camusdb.github.io/docs/transaction-priority
- https://camusdb.github.io/docs/best-practices
- https://camusdb.github.io/docs/row-level-ttl
- https://camusdb.github.io/docs/truncate-table
- https://camusdb.github.io/docs/connection-model
