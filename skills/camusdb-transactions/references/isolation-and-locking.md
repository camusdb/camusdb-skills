# Isolation and locking

Read this file when you choose an isolation level or a locking strategy, or when
you explain why a transaction conflicted.

## Contents

- Guarantees
- Isolation levels
- Read-only and read-write modes
- Locking strategy combinations
- Lock types
- Conflict resolution
- Schema pinning
- Where to set each option
- Server settings

## Guarantees

CamusDB gives these guarantees at all times:

- Writes are atomic. Commits are durable.
- There are no dirty reads.
- CamusDB detects write-write conflicts. It never merges two writes in silence.
- Execution is serializable by default.
- A write across partitions is atomic, through two-phase commit.

## Isolation levels

| Level | Reads | Protects against |
| --- | --- | --- |
| `SERIALIZABLE` | Snapshot, or locking scans in read-write mode | Read skew, phantoms, write skew, lost updates |
| `READ COMMITTED` | Committed MVCC versions, skipping write intents | Dirty reads and write-write conflicts only |

Read Committed keeps these properties:

- A read returns committed versions only.
- A write still uses locks, write intents, and atomic commit.
- A reader blocks no reader and no writer.

Read Committed gives up these properties:

- One snapshot shared by every sub-read of a query.
- Repeatable reads across statements.
- Phantom protection on a range scan.
- Write-skew protection.

Under pessimistic Read Committed, a point read can record the row revision when
the same transaction later writes that key. This is not a general
repeatable-read guarantee.

## Read-only and read-write modes

| Mode | How it works | Use for |
| --- | --- | --- |
| Serializable read-only | One HLC snapshot fixed at `BEGIN`. No locks. A client can resume it across requests with the transaction id. | Reports, multi-step reads, snapshots across partitions |
| Serializable read-write | MVCC plus shared point locks, shared range locks, and exclusive key protection on writes | Read-then-write invariants |

A read-only transaction creates no mutation and does not take the conflict
path. Choose it whenever the work does not write.

```sql
BEGIN;
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY;
SELECT COUNT(*) FROM orders WHERE status = 'open';
SELECT SUM(total) FROM orders WHERE status = 'open';
COMMIT;
```

## Locking strategy combinations

| Combination | Behavior |
| --- | --- |
| `SERIALIZABLE` + pessimistic | Default. CamusDB takes the locks first. |
| `SERIALIZABLE` + optimistic | Hybrid. Writes validate at commit. Reads and scans still take shared locks, so phantoms stay excluded. |
| `READ COMMITTED` + pessimistic | Writes lock. Reads see the latest committed version. |
| `READ COMMITTED` + optimistic | No locks at all. CamusDB validates observed rows only. No phantom protection. |

An optimistic transaction aborts at commit in two cases. Another transaction
wrote a key that this transaction wrote. Or a row that this transaction read
changed. The application then replays from `BEGIN`.

Choose optimistic when conflicts are rare and you want fewer lock waits. Keep
pessimistic when the same keys are contended. Under contention, an optimistic
transaction does all its work and then aborts at commit.

## Lock types

| Lock | Taken by | Effect |
| --- | --- | --- |
| Exclusive key lock plus write intent | Every write, for each modified row and index key | Detects write-write and unique-index conflicts |
| Shared point lock | A serializable read-write point read, such as a lookup by unique key | A writer to that key waits, retries, or aborts. Promoted to write protection if the same transaction writes the key. |
| Shared range lock | A serializable read-write scan | Overlapping scans still run together. A write into the range waits or retries. |
| Bucket lock (escalated) | Many point read locks in one table or index bucket | Bounded bookkeeping. Protects more rows than were read. |
| Foreign-key parent lock | A child write | A shared lock on each distinct parent key until the transaction ends |

The coordinator renews a live range lock while the transaction is alive. The
initial TTL (`range_lock_expires_ms`, default `150000`) is not a limit on run
time. The lifetime cap is.

## Conflict resolution

- CamusDB uses a deterministic ordering, called wait-die. An older transaction
  may wait. A younger transaction that would create a cycle aborts and must
  retry.
- A transaction that holds no lock and staged no write can safely wait for a
  short in-flight writer.
- One lock wait is capped by `lock_wait_deadline_ms` (default `500`). Past that,
  CamusDB reports a conflict.
- A `CADB0502` message includes bounded context: the table or database, a small
  sample of contended keys, and the waiting transaction mode when available.
  Use it to find the hot key.

Ways to reduce conflicts:

- Keep transactions short. Touch the keys of one entity.
- Read with a point lookup instead of a wide scan when you then write.
- Use a read-only snapshot transaction for reads that do not lead to a write.
- Shard hot rows. See `limits-and-batching.md`.
- Express read-modify-write as one `UPDATE` statement.

## Schema pinning

A transaction pins the schema version of each table it touches. At commit,
CamusDB checks that each table still exists and that the pinned version is
still compatible. A transaction that writes a table which gained an index or a
constraint aborts and must be replayed. CamusDB never commits a write that mixes
two layouts.

## Where to set each option

| Option | SQL | HTTP field | gRPC field | .NET | TypeScript |
| --- | --- | --- | --- | --- | --- |
| Isolation | `SET TRANSACTION ISOLATION LEVEL ...` | `isolationLevel`: `Serializable`, `ReadCommitted` | `isolation_level`: `SERIALIZABLE`, `READ_COMMITTED` | `IsolationLevel` | `isolationLevel` |
| Mode | `... SERIALIZABLE READ ONLY` | `transactionMode`: `ReadWrite`, `ReadOnly` | `transaction_mode`: `READ_WRITE`, `READ_ONLY` | `Mode` / `TransactionMode` | `mode` |
| Locking | `SET TRANSACTION LOCKING ...` | `locking`: `Pessimistic`, `Optimistic` | `locking`: `PESSIMISTIC`, `OPTIMISTIC` | `Locking` | `locking` |
| Priority | `SET TRANSACTION PRIORITY ...` | `priority` | `priority`; unspecified means the server default | not documented | not documented |

Precedence for priority: `SET TRANSACTION PRIORITY`, then the request field, then
`default_transaction_priority`, then `Normal`.

HTTP ignores these fields when a request resumes an existing transaction with
`txnIdPT` and `txnIdCounter`. A read-only `/execute-sql-query` ignores
`locking`.

## Server settings

```yaml
default_isolation_level: serializable        # or read_committed
default_transaction_locking: pessimistic     # or optimistic
default_transaction_priority: normal         # background | low | normal | high | critical
range_lock_expires_ms: 150000
max_serializable_transaction_lifetime_ms: 3600000
lock_escalation_threshold: 50
lock_wait_deadline_ms: 500
key_range_sharding: false
```

- `max_serializable_transaction_lifetime_ms` at `0` or below disables the
  lifetime cap.
- `range_lock_expires_ms` at `0` or below disables expiry. A positive value must
  be at least twice the Kahuna collection interval.

Docs:

- https://camusdb.github.io/docs/serializable-transactions
- https://camusdb.github.io/docs/sql-transactions
- https://camusdb.github.io/docs/transactions-locking-internals
- https://camusdb.github.io/docs/http-api
