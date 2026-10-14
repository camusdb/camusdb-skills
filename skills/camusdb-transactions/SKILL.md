---
name: camusdb-transactions
description: Guides writing and reviewing correct transactional application code for CamusDB. Covers BEGIN, START TRANSACTION, COMMIT, ROLLBACK, SET TRANSACTION ISOLATION LEVEL (SERIALIZABLE, READ ONLY, READ COMMITTED), SET TRANSACTION LOCKING PESSIMISTIC or OPTIMISTIC, SET TRANSACTION PRIORITY, wait-die conflicts, client retry loops for CADB0502, CADB0504, CADB0505, CADB0509 and CADB0506, the 20,000-mutation and one-hour limits, batching large UPDATE or DELETE jobs, hot-row contention and sharded counters, two-phase commit, and AS OF SYSTEM TIME historical reads. Use when writing CamusDB transaction code or retry logic, when using the .NET SerializableRetryHelper or the TypeScript transaction() and withRetry() helpers, or when porting transaction code from PostgreSQL, MySQL, CockroachDB, or Spanner to CamusDB.
---

# CamusDB transactions

CamusDB runs every statement inside a transaction. Execution is serializable by
default. A serializable transaction can abort by design, and CamusDB never
replays an explicit transaction for you. Correct application code therefore
needs three things: the right isolation and mode, a replayable transaction body,
and a retry loop that treats each error code differently.

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
2. Keep the default `SERIALIZABLE`. Choose `READ COMMITTED` only as a stated
   opt-out, with a reason.
3. Keep pessimistic locking unless contention is low and conflicts are rare.
4. Put every `SET TRANSACTION` statement right after `BEGIN`, before any read or
   write.
5. Make the body replayable. Move side effects (email, payment, queue publish)
   out of the body, or after a successful commit.
6. Wrap the body in a retry loop that follows the error table below.
7. Estimate the mutation count. Batch the work if it nears 20,000 mutations.
8. Keep the transaction short. Send all of its statements to the same node.
9. Check for a hot row that every transaction updates. Shard it.

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

- The three `SET TRANSACTION` forms may appear in any order.
- All of them must come before the first data statement. CamusDB rejects a late
  one. It does not ignore it.
- `SET TRANSACTION` applies to the current transaction only. CamusDB has no
  session-level `SET`.
- `TRUNCATE` cannot run inside an explicit transaction. It fails with
  `CADB0538 StatementNotAllowedInTransaction`.

## Isolation and mode

| Statement | Use for |
| --- | --- |
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` | Default. Read-write. Protects against read skew, phantoms, write skew, and lost updates. |
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY` | A report of several queries. One snapshot fixed at `BEGIN`. Takes no lock and blocks no writer. |
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE` | The explicit form of the default mode. |
| `SET TRANSACTION ISOLATION LEVEL READ COMMITTED` | An explicit opt-out. Read-write only. No read-only variant. |

Read Committed gives up repeatable reads, phantom protection, and write-skew
protection. One `UPDATE` or `DELETE` statement still protects itself from a lost
update. It locks the candidate rows, reads them again, and evaluates `WHERE`
again. So express read-modify-write as one statement when you can:

```sql
-- Safe under both levels: adds to the latest committed balance.
UPDATE accounts SET balance = balance + 1 WHERE id = @id;
```

A `SELECT` followed by an `UPDATE` in a later statement is not protected under
Read Committed. Use Serializable for that pattern.

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
  then reports a conflict.
- Many point read locks in one bucket escalate to one bucket lock. The default
  `lock_escalation_threshold` is `50`. A large read therefore protects more
  rows than it read, and conflicts can rise.

Read [references/isolation-and-locking.md](references/isolation-and-locking.md)
when you choose between the combinations or tune lock settings.

## Retry contract

| Code | Name | Action |
| --- | --- | --- |
| `CADB0502` | `TransactionConflict` | Roll back. Replay the whole transaction from `BEGIN`. |
| `CADB0504` | `TransactionMustRetry` | Roll back. Replay from `BEGIN`, with backoff. |
| `CADB0505` | `TransactionLifetimeExceeded` | Roll back. Replay from `BEGIN`. Make the transaction shorter. |
| `CADB0509` | `TransactionFinalizeUnresolved` | Send the same `COMMIT` or `ROLLBACK` again, for the same transaction id. Never replay the work. |
| `CADB0506` | `TransactionMutationLimitExceeded` | Do not retry. Split the work into smaller transactions. |
| `CADB0503` | `SchemaCatchingUp` | Retry later, or against another node. |

Every other code needs a change to the request first. See
https://camusdb.github.io/docs/error-codes for the full list.

Language-neutral loop:

```text
for attempt in 1..MAX_ATTEMPTS:            # 5 matches both driver helpers
    txn = BEGIN(options)
    try:
        result = body(txn)                 # every read and write, no external side effects
        commit_resolving_0509(txn)         # resend the SAME COMMIT while CADB0509
        return result
    catch error:
        rollback_quietly(txn)              # ROLLBACK is idempotent by transaction id
        if error.code in {CADB0502, CADB0504, CADB0505} and attempt < MAX_ATTEMPTS:
            sleep(min(20ms * 2^attempt, 400ms) +/- 25% jitter)
            continue
        raise error                        # CADB0506 and all other codes are not replayable
```

Rules for the loop:

- Restart from `BEGIN`. Run every read again. A retry of only the failed
  statement is wrong, because the transaction is dead.
- Never put `CADB0509` in the replay set. The commit may already have applied.
- If `CADB0509` persists past your bound, report the outcome as unknown. Check
  the database state before you run the business operation again.
- Always use backoff. `CADB0504` can mean that a saturated node shed the
  transaction at admission.

Read [references/retry-patterns.md](references/retry-patterns.md) for full .NET,
TypeScript, and HTTP/gRPC versions of this loop.

## Driver helpers

| Driver | Helper | Scope |
| --- | --- | --- |
| .NET | `SerializableRetryHelper.ExecuteAutocommitAsync(op, maxAttempts: 5, ct)` | Replays the delegate on `CADB0502`, `CADB0504`, `CADB0505`. Documented for single-statement work. |
| .NET | `SerializableRetryHelper.IsRetryable(ex)` | Use it in your own loop for a multi-statement transaction. |
| TypeScript | `client.transaction(async (txn) => { ... })` | Begins, commits, rolls back on throw, and replays the whole function on a retryable error. |
| TypeScript | `withRetry(async (attempt) => { ... })` | Wraps your own idempotent unit of work. |

Both drivers resolve `CADB0509` inside commit and rollback. They resend the same
finalize on the same handle. Neither helper replays the work on `CADB0509`.

## Server-side autocommit retries

- CamusDB retries an autocommit statement on the server, with a bound.
- A streaming HTTP query in autocommit runs one attempt only. Rows may already
  be on the wire. Use `/execute-sql-query` when an automatic retry matters more.
- The server never replays an explicit transaction. The client restarts it.

## Limits

| Limit | Default | Setting | On breach |
| --- | --- | --- | --- |
| Serializable read-write lifetime | 1 hour | `max_serializable_transaction_lifetime_ms` | `CADB0505`, retryable |
| Mutations per transaction | 20,000 | `max_mutations_per_transaction` | `CADB0506`, not retryable |
| Idle time inside an explicit transaction | 5 minutes | `transaction_idle_timeout_ms` | CamusDB rolls the transaction back |
| Finalize retry budget on the server | 15 seconds | `transaction_finalize_retry_budget_ms` | `CADB0509` |

Mutation cost per row, with `K` secondary indexes and `L` out-of-line large
values:

| Operation | Mutations |
| --- | --- |
| Insert one row | `2 + K + L` |
| Delete one row | `2 + K + L` |
| Update one row, changing `M` indexed columns | `1 + 2M` |

A read-only transaction creates no mutation. Two updates of the same row count
twice.

## Batching large writes

Split a large `UPDATE` or `DELETE` into bounded key ranges. Run one transaction
per range:

```sql
UPDATE orders
SET status = 'shipped'
WHERE shipped_at IS NOT NULL
  AND id >= @start_id
  AND id < @end_id;
```

- Size each batch well below 20,000 mutations. Divide by the per-row cost.
- Make each batch idempotent. A replayed batch must give the same result.
- Use `SET TRANSACTION PRIORITY BACKGROUND` for deferrable bulk work.
- To empty a whole table, run `TRUNCATE` in autocommit. It counts no mutation.
- For expiry, prefer row-level TTL over hand-written batch deletes.

Read [references/limits-and-batching.md](references/limits-and-batching.md) for
worked examples, TTL, and hot-row patterns.

## Hot rows

A row that every transaction updates makes all writers conflict, with
`CADB0502` storms. Shard it:

```sql
CREATE TABLE stat_counters (
  name STRING(64) NOT NULL,
  shard INT64 NOT NULL,
  value INT64 NOT NULL
) PRIMARY KEY (name, shard);

UPDATE stat_counters SET value = value + 1
WHERE name = 'matches_played' AND shard = @shard;   -- pick a random shard

SELECT SUM(value) AS total FROM stat_counters WHERE name = 'matches_played';
```

## Priority

`SET TRANSACTION PRIORITY { BACKGROUND | LOW | NORMAL | HIGH | CRITICAL }`
orders admission only. It applies when a node is saturated and the admission
gate is on (`kahuna.max_concurrent_sessions` above `0`). The gate is off by
default. Priority does not change isolation, locks, commit order, or resources.
Do not tag ordinary traffic as `CRITICAL`.

## Distributed transactions

A transaction that touches several partitions commits through two-phase commit.
It commits on every partition or on none. HLC timestamps order transactions.
The order is logically consistent. CamusDB gives no commit-wait or real-time
external consistency guarantee. Distributed transaction support is an alpha
feature.

An explicit transaction lives in memory on the node that started it. Send every
statement, and the `COMMIT` or `ROLLBACK`, to that node. Other nodes reject the
handle.

Read [references/distributed-and-historical.md](references/distributed-and-historical.md)
for 2PC, HLC, and historical reads.

## Historical reads

```sql
SELECT player_id, score
FROM leaderboard AS OF SYSTEM TIME '-10s'
ORDER BY score DESC
LIMIT 100;
```

- Use it for dashboards and reports that tolerate a few seconds of staleness.
  It blocks no writer.
- It works in autocommit only. Inside an explicit transaction it fails with
  `CADB0409`. Use a `SERIALIZABLE READ ONLY` transaction instead.
- It may be the source of `INSERT INTO ... SELECT` or `CREATE TABLE ... AS
  SELECT`. Not of `UPDATE`, `DELETE`, or `INSERT ... VALUES`.

## Common mistakes

- **Retrying the failed statement** (PostgreSQL savepoint habit). CamusDB
  documents no `SAVEPOINT`. Replay the whole transaction from `BEGIN`.
- **`SELECT ... FOR UPDATE`** (PostgreSQL, MySQL). CamusDB documents no such
  clause. Use the serializable read-write default. Its read locks are promoted
  when the same transaction writes the key. Or write one `UPDATE` statement.
- **`BEGIN ISOLATION LEVEL ...` or `START TRANSACTION READ ONLY`.** Write `BEGIN`,
  then `SET TRANSACTION ...` as the next statement.
- **`REPEATABLE READ`, `READ UNCOMMITTED`, or `SNAPSHOT`.** CamusDB accepts only
  `SERIALIZABLE` and `READ COMMITTED`.
- **Session settings** such as `SET SESSION CHARACTERISTICS` or `SET
  default_transaction_isolation`. CamusDB has no session state. Set the level
  per transaction, per request, or in driver options.
- **Expecting automatic retry of an explicit transaction** (CockroachDB habit).
  CamusDB retries autocommit statements only.
- **Replaying after an ambiguous commit** (CockroachDB `40003` habit). On
  `CADB0509`, resend the same `COMMIT`. Do not replay.
- **Retrying `CADB0506`** (Spanner "too many mutations" habit). The same batch
  fails again. Note that CamusDB counts the row, the primary-key entry, each
  index entry, and each out-of-line value.
- **`BEGIN ... AS OF SYSTEM TIME`** (CockroachDB). CamusDB rejects historical
  reads inside a transaction. Use a read-only snapshot transaction.
- **Expecting a "too old" error** for an old `AS OF SYSTEM TIME`. CamusDB can
  return an empty result instead.
- **Expecting real-time external consistency** (Spanner TrueTime habit). The
  HLC order is logical only.
- **Treating priority as a lock or CPU control** (Spanner priority habit). A
  `BACKGROUND` lock holder blocks a `CRITICAL` transaction as hard as any other.
- **`TRUNCATE` inside `BEGIN`.** It fails with `CADB0538`. Run it in autocommit.
- **Long pauses inside a transaction**, such as waiting for user input. An idle
  transaction rolls back after 5 minutes and holds locks until then.
- **A transaction pooler** such as PgBouncer. CamusDB needs none, and a
  transaction must stay on its own node.

## References

Read these files when the task needs the detail:

- [references/retry-patterns.md](references/retry-patterns.md): full retry code
  for .NET, TypeScript, and raw HTTP or gRPC.
- [references/isolation-and-locking.md](references/isolation-and-locking.md):
  isolation and locking combinations, lock settings, schema pinning.
- [references/limits-and-batching.md](references/limits-and-batching.md):
  mutation math, batching, TTL, hot rows, priority gate settings.
- [references/distributed-and-historical.md](references/distributed-and-historical.md):
  2PC, HLC, durability, `AS OF SYSTEM TIME` rules.

Public docs:

- https://camusdb.github.io/docs/sql-transactions
- https://camusdb.github.io/docs/serializable-transactions
- https://camusdb.github.io/docs/serializable-retries
- https://camusdb.github.io/docs/transaction-limits
- https://camusdb.github.io/docs/transaction-priority
- https://camusdb.github.io/docs/distributed-transactions
- https://camusdb.github.io/docs/transactions-locking-internals
- https://camusdb.github.io/docs/time-travel-reads
- https://camusdb.github.io/docs/connection-model
- https://camusdb.github.io/docs/best-practices
- https://camusdb.github.io/docs/error-codes
- https://camusdb.github.io/docs/dotnet-driver
- https://camusdb.github.io/docs/typescript-connector
