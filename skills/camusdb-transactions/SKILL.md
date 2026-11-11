---
name: camusdb-transactions
description: Guides writing and reviewing correct transactional application code for CamusDB. Covers BEGIN, START TRANSACTION, COMMIT, ROLLBACK, SET TRANSACTION ISOLATION LEVEL (SERIALIZABLE, READ ONLY, READ COMMITTED), SET TRANSACTION LOCKING PESSIMISTIC or OPTIMISTIC, SET TRANSACTION PRIORITY, wait-die conflicts, failed statements that abort a transaction (CADB0824, no savepoints), SELECT FOR UPDATE with NOWAIT or SKIP LOCKED, job queues, INSERT ON CONFLICT and UPSERT, driver retry helpers and client retry loops for CADB0502, CADB0504, CADB0505, CADB0509, CADB0503, CADB0506 and CADB0555, the 20,000-mutation and one-hour limits, batching large writes, hot rows and sharded counters, two-phase commit, and AS OF SYSTEM TIME reads. Use when writing CamusDB transaction code or retry logic, when using the .NET SerializableRetryHelper, the TypeScript transaction() and withRetry() helpers, or Python camusdb.run_transaction(), or when porting transaction code from PostgreSQL, MySQL, CockroachDB, or Spanner to CamusDB.
---

# CamusDB transactions

CamusDB runs every statement inside a transaction. Execution is serializable by
default. A serializable transaction can abort by design. The server never
replays an explicit transaction, but the .NET, TypeScript, and Python drivers
have retry helpers that replay it for you. Correct application code therefore
needs three things: the right isolation and mode, a replayable callback, and a
driver retry helper (or, without a driver, a loop that treats each code differently).

Use this skill to write, review, or port transactional code. For an error that
you must diagnose, use the `camusdb-troubleshooting` skill.

## Workflow

Follow these steps for each unit of work:

1. Classify the work.
   - One statement: run it in autocommit. The server retries it with a bound.
   - Several writes that must commit together: use an explicit read-write
     transaction.
   - A consistent report of several queries: use `SERIALIZABLE READ ONLY`.
   - A read of a past state: use `AS OF SYSTEM TIME` in autocommit.
   - An insert that may hit an existing key: use `INSERT ... ON CONFLICT`.
   - A read that decides a later write on the same row: use `FOR UPDATE`.
2. Keep the default `SERIALIZABLE`. Choose `READ COMMITTED` only as a stated
   opt-out, with a reason.
3. Keep pessimistic locking unless contention is low and conflicts are rare.
4. Put every `SET TRANSACTION` statement right after `BEGIN`, before any read or
   write.
5. Make the body replayable. Move side effects (email, payment, queue publish)
   out of the body, or after a successful commit.
6. Do not catch a statement error and continue in the same transaction. Any
   failed statement aborts it. Let the error reach the retry helper.
7. Wrap the whole unit of work in the driver's retry helper. Write your own loop
   only for a custom protocol client.
8. Estimate the mutation count. Batch the work if it nears 20,000 mutations.
9. Keep the transaction short. Let the driver keep it on its node.
10. Check for a hot row that every transaction updates. Shard it.

## Transaction syntax

```sql
BEGIN;                      -- or: START TRANSACTION;
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;   -- optional, the default
SET TRANSACTION LOCKING PESSIMISTIC;            -- optional, the default
SET TRANSACTION PRIORITY NORMAL;                -- optional, the default
UPDATE accounts SET balance = balance - 100 WHERE id = @from;
UPDATE accounts SET balance = balance + 100 WHERE id = @to;
COMMIT;                     -- or: ROLLBACK;
```

Rules:

- All of them must come before the first data statement. CamusDB rejects a late
  one. It does not ignore it.
- `TRUNCATE`, `ALTER TABLE ... REWRITE STORAGE`, and `ALTER SEQUENCE ... RESTART`
  cannot run inside an explicit transaction. Each fails with
  `CADB0538 StatementNotAllowedInTransaction`, and the failure aborts the
  transaction.

## Isolation and mode

| Statement | Use for |
| --- | --- |
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` | Default. Read-write. Protects against read skew, phantoms, write skew, and lost updates. |
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY` | A report of several queries. One snapshot fixed at `BEGIN`. Takes no lock and blocks no writer. |
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE` | The explicit form of the default mode. |
| `SET TRANSACTION ISOLATION LEVEL READ COMMITTED` | An explicit opt-out. Read-write only. No read-only variant. |

In a pessimistic Read Committed transaction, each `SELECT` reads one statement
snapshot and sees the transaction's own writes. The next statement gets a new
snapshot. Scans take no range lock in any routing mode.

Read Committed gives up repeatable reads across statements, phantom protection,
and write-skew protection. One `UPDATE` or `DELETE` statement still protects
itself from a lost update. It locks the candidate rows, reads them again, and
evaluates `WHERE` again. So express read-modify-write as one statement when you
can:

```sql
-- Safe under both levels: adds to the latest committed balance.
UPDATE accounts SET balance = balance + 1 WHERE id = @id;
```

A plain `SELECT` followed by an `UPDATE` in a later statement is not protected
under Read Committed. Use Serializable or `SELECT ... FOR UPDATE` for that
pattern.

An autocommit `SELECT` reads one pinned snapshot under either default level, with
no lock. For read-your-writes across nodes, the .NET and TypeScript gRPC clients
carry causal tokens for you. Python does not track them. See the isolation
reference for commit waits and the raw token rules for custom clients.

## Locking and conflicts

- Pessimistic (default): CamusDB takes locks before conflicting work continues.
  Conflicts usually surface at lock acquisition.
- Optimistic: writes skip the exclusive lock and validate at commit. Under
  `SERIALIZABLE`, reads and scans still take shared locks, so phantoms stay
  excluded. Under `READ COMMITTED`, optimistic takes no lock at all.
- Wait-die resolves a conflict between serializable transactions. An older
  transaction may wait. A younger transaction that would create a cycle aborts
  and must retry.
- One lock wait is capped by `lock_wait_deadline_ms` (default `500`). CamusDB
  then reports a retryable error, `CADB0502` or `CADB0504`.
- Many point read locks in one bucket escalate to one bucket lock (default
  `lock_escalation_threshold` is `50`). Conflicts can then rise.

Read [references/isolation-and-locking.md](references/isolation-and-locking.md)
when you choose between the combinations or tune lock settings.

## A failed statement aborts the transaction

Any statement that fails inside an explicit transaction aborts it. This includes
a read, a cancelled statement, and a statement refused inside a transaction
block. CamusDB has no savepoints. To continue, roll back and start a new
transaction.

| Next action | Engine rule | Over HTTP and gRPC |
| --- | --- | --- |
| Any statement, reads included | `CADB0824 TransactionRollbackOnly` | `CADB0400` "Unknown transaction" |
| `COMMIT` | Rolls back, reports `CADB0824` | `CADB0400` "Unknown transaction" |
| `ROLLBACK` | Ends the transaction | Succeeds as a no-op |

HTTP and gRPC roll the transaction back as soon as the statement fails. The
`CADB0824` message names the statement that failed. No earlier write commits.

Code that catches an error, such as a duplicate key, and then runs more statements
is wrong. Those statements and the `COMMIT` fail, and nothing is stored. Prevent
the expected error in SQL instead:

```sql
BEGIN;
INSERT INTO users (id, email) VALUES (@id, @email)
  ON CONFLICT (id) DO NOTHING;
UPDATE users SET last_seen = @now WHERE id = @id;
COMMIT;
```

## Row locks and upserts

`SELECT ... FOR UPDATE [OF t] [NOWAIT | SKIP LOCKED]` locks each returned row
until the transaction ends. It needs a pessimistic read-write transaction. A held
row gives `CADB0502`. With `NOWAIT` it gives `CADB0555`. `SKIP LOCKED` leaves the
row out and works at Read Committed only, so a job queue runs at that level:

```sql
BEGIN;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT id FROM jobs WHERE state = 'ready' ORDER BY id LIMIT 10 FOR UPDATE SKIP LOCKED;
UPDATE jobs SET state = 'running' WHERE id IN (@id1, @id2);
COMMIT;
```

Use `INSERT ... ON CONFLICT` or `UPSERT INTO` instead of a read followed by an
insert or update. It locks each key before it reads:
`ON CONFLICT (k) DO UPDATE SET n = counters.n + excluded.n`.

Read [references/locking-reads-and-upserts.md](references/locking-reads-and-upserts.md)
for the refused shapes, autocommit behavior, arbiter rules, and upsert conflicts.

## Retry with the driver helpers

Put the whole unit of work in a callback. The helper recognizes retryable
conflicts, rolls back, and runs the callback again in a fresh transaction, with
bounded backoff and jitter. Each helper defaults to five attempts in total.

| Driver | Helper | What it does |
| --- | --- | --- |
| .NET | `SerializableRetryHelper.ExecuteAutocommitAsync(async ct => { ... }, maxAttempts: 5)` | Repeats the callback. For an explicit transaction, begin and commit inside the callback. |
| TypeScript | `client.transaction(async (txn) => { ... }, { maxAttempts: 5 })` | Begins, commits, rolls back on a throw, and replays the callback. `withRetry(...)` wraps your own unit of work. |
| Python | `camusdb.run_transaction(conn, callback, max_attempts=5)` | Manages the transaction and replays the callback. `await camusdb.aio.run_transaction(...)` is the asyncio form. Start with no open transaction. |

```csharp
await SerializableRetryHelper.ExecuteAutocommitAsync(async ct =>
{
    await using var tx = await connection.BeginTransactionAsync(ct);
    await using var command = connection.CreateCamusCommand(
        "UPDATE products SET stock = stock - 1 WHERE name = @name AND stock > 0");
    command.Transaction = tx;
    command.Parameters.Add("@name", ColumnType.String, "kettle");
    await command.ExecuteNonQueryAsync(ct);
    await tx.CommitAsync(ct);
}, maxAttempts: 5);
```

Rules for the callback:

- Put every read and write of the operation inside it, so a replay reads fresh data.
- Do not catch a statement error inside it. Let the error reach the helper.
- Keep email, payment, and other external side effects outside it. It can run
  more than once.
- Plain commands and hand-managed transaction blocks do not replay a whole
  business operation. Use the helper for that boundary.

## Retry contract

The helpers implement this contract. You normally do not dispatch on the codes:

| Code | Name | Recovery |
| --- | --- | --- |
| `CADB0502` | `TransactionConflict` | The helper replays the callback. |
| `CADB0504` | `TransactionMustRetry` | The helper replays the callback, with backoff. |
| `CADB0505` | `TransactionLifetimeExceeded` | The helper replays the callback. Make the transaction shorter. |
| `CADB0509` | `TransactionFinalizeUnresolved` | The commit or rollback method resends the same finalize on the same transaction. It does not replay the callback. The work may have committed. |
| `CADB0503` | `SchemaCatchingUp` | Not in the helpers' set. Retry after the node catches up, or on another node. |
| `CADB0506` | `TransactionMutationLimitExceeded` | Do not retry. Split the work into smaller transactions. |
| `CADB0555` | `LockNotAvailable` | Not retried: `FOR UPDATE NOWAIT` asked not to wait. The transaction ended. |
| `CADB0558` | `ConcurrencyLimitExceeded` | The node has too many open explicit transactions. Retry `BEGIN` after a back-off. |
| `CADB0824` | `TransactionRollbackOnly` | Do not replay as is. An earlier error was swallowed. Fix that code. HTTP and gRPC show `CADB0400` "Unknown transaction" instead. |

If `CADB0509` still reaches your code after the driver's retries, resend the
same finalize on the same handle. Do not start the business operation again.
Every other code needs a change to the request first. See
https://camusdb.github.io/docs/error-codes for the full list.

Read [references/retry-patterns.md](references/retry-patterns.md) for the full
helper code and for a hand-written loop for HTTP or gRPC clients.

## Server-side autocommit retries

The server retries an autocommit statement, with a bound. A streaming HTTP query
in autocommit runs one attempt only, because rows may already be on the wire. The
server never replays an explicit transaction. The client restarts it.

## Limits

| Limit | Default | Setting | On breach |
| --- | --- | --- | --- |
| Serializable read-write lifetime | 1 hour | `max_serializable_transaction_lifetime_ms` | `CADB0505`, retryable |
| Mutations per transaction | 20,000 | `max_mutations_per_transaction` | `CADB0506`, not retryable |
| Idle time inside an explicit transaction | 5 minutes | `transaction_idle_timeout_ms` | CamusDB rolls it back. The next statement gets `CADB0400` "Unknown transaction". |
| Open explicit transactions per node | 10,000 | `max_open_explicit_transactions` | `CADB0558`, retry `BEGIN` after a back-off |
| Finalize retry budget on the server | 15 seconds | `transaction_finalize_retry_budget_ms` | `CADB0509` |

An insert or delete of one row costs `2 + K + L` mutations, with `K` secondary
indexes and `L` out-of-line values. An update changing `M` indexed columns costs
`1 + 2M`. A read-only transaction creates no mutation.

## Batching large writes

Split a large `UPDATE` or `DELETE` into bounded key ranges, such as
`id >= @start_id AND id < @end_id`. Run one transaction per range.

- Size each batch well below 20,000 mutations. Divide by the per-row cost.
- Make each batch idempotent. A replayed batch must give the same result.
- Use `SET TRANSACTION PRIORITY BACKGROUND` for deferrable bulk work.
- To empty a whole table, run `TRUNCATE` in autocommit. It counts no mutation.
- For expiry, prefer row-level TTL over hand-written batch deletes.

Read [references/limits-and-batching.md](references/limits-and-batching.md) for
worked examples, TTL, and hot-row patterns.

## Hot rows and priority

A row that every transaction updates makes all writers conflict, with
`CADB0502` storms. Shard it: spread the writes across N rows, pick a random shard
per write, and sum on read. See
[references/limits-and-batching.md](references/limits-and-batching.md).

`SET TRANSACTION PRIORITY { BACKGROUND | LOW | NORMAL | HIGH | CRITICAL }` orders
admission only, when the gate is on (`kahuna.max_concurrent_sessions` above `0`).
The gate is off by default. Priority changes no isolation, lock, or commit order.

## Distributed transactions

A transaction across partitions commits through two-phase commit, on every
partition or on none. HLC timestamps give a logical order, with no real-time
guarantee. The isolation choices are the same on one node and in a cluster.

An explicit transaction lives on the node that started it. The drivers keep its
statements, and the `COMMIT` or `ROLLBACK`, on that node. A custom protocol
client must keep that affinity itself. Other nodes reject the handle.

Read [references/distributed-and-historical.md](references/distributed-and-historical.md)
for 2PC, HLC, and historical reads.

## Historical reads

```sql
SELECT player_id, score FROM leaderboard AS OF SYSTEM TIME '-10s' LIMIT 100;
```

- Use it for dashboards and reports that tolerate a few seconds of staleness.
  It blocks no writer.
- It works in autocommit only. Inside an explicit transaction it fails with
  `CADB0409`. Use a `SERIALIZABLE READ ONLY` transaction instead.

## Common mistakes

- **Catching an error and continuing in the same transaction** (MySQL and Oracle
  habit). Any failed statement aborts the transaction. Roll back and start a new
  one. Use `ON CONFLICT` for an expected duplicate.
- **Retrying the failed statement** (PostgreSQL savepoint habit). CamusDB has no
  savepoints. Replay the whole callback through the driver's retry helper.
- **Reading, then inserting if absent.** Use `INSERT ... ON CONFLICT` or `UPSERT
  INTO`. `INSERT ... ON DUPLICATE KEY UPDATE` and `MERGE` are not supported.
- **`FOR SHARE`, or `SKIP LOCKED` at Serializable.** Both fail with `CADB0533`.
- **`FOR UPDATE` in autocommit to protect a later request.** The locks end with
  the statement. Use an explicit transaction.
- **`FOR UPDATE` in a read-only or optimistic transaction, or with `AS OF SYSTEM
  TIME`, a join, `GROUP BY`, or `DISTINCT`.** These fail with `CADB0400`.
- **`BEGIN ISOLATION LEVEL ...` or `START TRANSACTION READ ONLY`.** Write `BEGIN`,
  then `SET TRANSACTION ...` as the next statement.
- **`REPEATABLE READ`, `READ UNCOMMITTED`, or `SNAPSHOT`.** CamusDB accepts only
  `SERIALIZABLE` and `READ COMMITTED`.
- **Session settings** such as `SET SESSION CHARACTERISTICS`. CamusDB has no
  session state. Set the level per transaction, per request, or in driver options.
- **Replaying after an ambiguous commit** (CockroachDB `40003` habit). On
  `CADB0509`, resend the same `COMMIT`. Do not replay.
- **Retrying `CADB0506`** (Spanner "too many mutations" habit). The same batch
  fails again. CamusDB counts the row and every key and out-of-line value.
- **A transaction pooler** such as PgBouncer. CamusDB needs none, and a
  transaction must stay on its own node.

## References

Read these files when the task needs the detail:

- [references/retry-patterns.md](references/retry-patterns.md): full retry code
  for .NET, TypeScript, and raw HTTP or gRPC.
- [references/isolation-and-locking.md](references/isolation-and-locking.md):
  isolation and locking combinations, statement snapshots, causal tokens, lock
  settings, schema pinning.
- [references/locking-reads-and-upserts.md](references/locking-reads-and-upserts.md):
  `SELECT ... FOR UPDATE`, job queues, `INSERT ... ON CONFLICT`, `UPSERT INTO`.
- [references/limits-and-batching.md](references/limits-and-batching.md):
  mutation math, batching, TTL, hot rows, priority gate settings.
- [references/distributed-and-historical.md](references/distributed-and-historical.md):
  2PC, HLC, durability, `AS OF SYSTEM TIME` rules.

Public docs:

- https://camusdb.github.io/docs/sql-transactions
- https://camusdb.github.io/docs/sql-queries#for-update
- https://camusdb.github.io/docs/sql-writes#on-conflict
- https://camusdb.github.io/docs/serializable-transactions
- https://camusdb.github.io/docs/serializable-retries
- https://camusdb.github.io/docs/transaction-limits
- https://camusdb.github.io/docs/error-codes
- https://camusdb.github.io/docs/dotnet-driver,
  https://camusdb.github.io/docs/typescript-connector, and
  https://camusdb.github.io/docs/python-driver
