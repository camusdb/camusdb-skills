# Isolation and locking

Read this file when you choose an isolation level or a locking strategy, or when
you explain why a transaction conflicted.

## Contents

- Guarantees
- Isolation levels
- Read-only and read-write modes
- Reads at a statement snapshot
- Correlated subqueries in writes
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
- A failed statement aborts its explicit transaction. See `retry-patterns.md`.

## Isolation levels

| Level | Reads | Protects against |
| --- | --- | --- |
| `SERIALIZABLE` | Snapshot, or locking scans in read-write mode | Read skew, phantoms, write skew, lost updates |
| `READ COMMITTED` | Pessimistic: one statement snapshot per `SELECT`, plus the transaction's own writes. Optimistic or read validation: the latest committed version of each key. | Dirty reads and write-write conflicts only |

Read Committed keeps these properties:

- A read returns committed versions and the transaction's own writes only.
- A write still uses locks, write intents, and atomic commit.
- A read takes no lock, so it blocks no writer. A scan takes no range lock in any
  routing mode.
- In a pessimistic transaction, one `SELECT` never sees half of another commit.
  The locate scan of an `UPDATE` or `DELETE`, and the source of an `INSERT ...
  SELECT`, also read one statement snapshot.
- The write phase reads the latest value. It locks each located row, reads it
  again, and checks `WHERE` again. Uniqueness and foreign-key checks also read
  the latest value.

Read Committed gives up these properties:

- One snapshot across statements. The next statement gets a new snapshot.
- Repeatable reads across statements.
- Phantom protection on a range scan.
- Write-skew protection.

A `SELECT` does not pin what it read. A later statement is never refused because
a key it read earlier changed. Nothing guards a value that you read in one
statement and write in a later one. That pattern needs Serializable, `SELECT ...
FOR UPDATE`, or a single `UPDATE ... SET v = f(v)`.

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

## Reads at a statement snapshot

These reads use a timestamp from the node's clock, taken when the statement
starts: a pessimistic Read Committed statement, and an autocommit `SELECT` under
either default level. An autocommit `SELECT` takes no lock and makes no Kahuna
round trip, in every routing mode. Such a read has two costs:

- **It can wait for a commit in progress.** It does not wait for a transaction that
  is still open. It waits only for one that started its commit. The limit is about
  5 s for a scan page and about 16 s for a point read. The statement then fails
  with a retryable error. CamusDB does not resend a read after 15 s of attempts.
- **It can miss a commit from another node.** A commit made at a later clock time
  stays invisible, even when it finished first. For read-your-writes across
  nodes, use the session behavior of your driver. The .NET and TypeScript gRPC
  clients propagate causal tokens for you, so application queries do not copy
  token fields. Python does not track causal tokens yet.

A custom protocol client forwards the complete causal token of the last commit
to the next autocommit query or `BEGIN`. It sets a minimum snapshot timestamp:

| Transport | Commit returns | Pass it to |
| --- | --- | --- |
| HTTP | `causalToken` in `/commit-transaction` and in autocommit responses | `causalToken` on `/start-transaction` or `/execute-sql-query` |
| gRPC | `causal_token_*` in `CommitReply` | `causal_token_*` on `StartTxnRequest`, a batch `START`, or an autocommit `SqlRequest` |

The server does not read the token on `TxnHandle`, or on a statement inside an
explicit transaction. A change to it after the start does not advance the
snapshot floor. Give the transaction its floor at the start.

## Correlated subqueries in writes

A correlated subquery in `UPDATE ... SET`, `UPDATE ... WHERE`, or `DELETE ...
WHERE` uses the target row as its outer scope. It reads the target table and
the other tables at the statement timestamp. So it does not see changes that
the same statement made earlier:

```sql
UPDATE app_users
SET post_count = (SELECT COUNT(*) FROM posts p WHERE p.user_id = app_users.id)
WHERE active = true;
```

## Locking strategy combinations

| Combination | Behavior |
| --- | --- |
| `SERIALIZABLE` + pessimistic | Default. CamusDB takes the locks first. |
| `SERIALIZABLE` + optimistic | Hybrid. Writes validate at commit. Reads and scans still take shared locks, so phantoms stay excluded. |
| `READ COMMITTED` + pessimistic | Writes lock. Each `SELECT` reads one statement snapshot. |
| `READ COMMITTED` + optimistic | No locks at all. Reads see the latest committed value. CamusDB validates observed rows only. No phantom protection. |

An optimistic transaction aborts at commit in two cases. Another transaction
wrote a key that this transaction wrote. Or a row that this transaction read
changed. The application then replays from `BEGIN`.

An optimistic transaction records a dependency on every key that a read touched.
This includes rows that a scan visited and `WHERE` then rejected. A committed write
to any column of such a row invalidates it. So an optimistic bulk scan of a busy
table does not commit while the writers run, at either isolation level. For that
read, use `AS OF SYSTEM TIME` or pessimistic locking.

Choose optimistic when conflicts are rare and you want fewer lock waits. Keep
pessimistic when the same keys are contended. Under contention, an optimistic
transaction does all its work and then aborts at commit.

## Lock types

| Lock | Taken by | Effect |
| --- | --- | --- |
| Exclusive key lock plus write intent | Every write, for each modified row and index key | Detects write-write and unique-index conflicts |
| Exclusive row lock | `SELECT ... FOR UPDATE`, at every isolation level, on each returned row | Another transaction cannot update, delete, or lock the row. A plain `SELECT` is not blocked. |
| Shared point lock | A serializable read-write read of a row or index entry by key: a primary-key read, a row fetched through a secondary index, a unique join probe, and the inner lookup of a semi-join or `EXISTS` | A writer to that key waits, retries, or aborts. A key read as absent is locked too. Promoted to write protection if the same transaction writes the key. |
| Shared range lock | A serializable read-write scan, including join leaves, the inner scan of `IN (SELECT ...)` and `EXISTS`, and a non-unique index probe in a join | Overlapping scans still run together. A write into the range waits or retries. A join leaf filter that no index serves locks the whole table. |
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
  CamusDB reports a retryable error, `CADB0502` or `CADB0504`.
- A key that is still replicating or settling is not a lock conflict. It waits up
  to `replication_wait_deadline_ms` (default `5000`), then fails with `CADB0504`.
  The message names the budget that ran out.
- A commit that the coordinator refuses (`CADB0502`) names the refusal class, the
  reason, the staged mutation count, and the finalize time.
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
constraint aborts and must be replayed. So does one whose table changed its row
layout after the statement was planned, at the commit or at the next statement on
that table. Both give `CADB0502`. CamusDB never commits a write that mixes two
layouts.

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
replication_wait_deadline_ms: 5000
max_open_explicit_transactions: 10000
key_range_sharding: false                    # fixed when the cluster is created
```

- `max_serializable_transaction_lifetime_ms` at `0` or below disables the
  lifetime cap.
- `range_lock_expires_ms` at `0` or below disables expiry. A positive value must
  be at least twice the Kahuna collection interval.

Docs:

- https://camusdb.github.io/docs/serializable-transactions#read-committed-snapshots-and-limits
- https://camusdb.github.io/docs/sql-transactions
- https://camusdb.github.io/docs/transactions-locking-internals
- https://camusdb.github.io/docs/http-api
