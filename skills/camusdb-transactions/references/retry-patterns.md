# Retry patterns

Read this file when you write or review the retry code for a CamusDB
transaction. It gives the full contract, then one version per client.

Use the driver helpers in application code. The .NET, TypeScript, and Python
helpers recognize retryable conflicts and replay your callback, so you do not
write a loop or a backoff. A hand-written loop is for a client that manages the
transaction lifecycle itself, or that speaks HTTP or gRPC without a helper.

## Contents

- The contract
- A failed statement aborts the transaction
- Backoff
- .NET driver
- TypeScript connector
- Python driver
- Raw HTTP
- gRPC
- Review checklist

## The contract

| Code | Meaning | What the transaction is | Client action |
| --- | --- | --- | --- |
| `CADB0502` `TransactionConflict` | A lock conflict, a conflicting concurrent write, a commit that returned `Aborted`, or a table that gained an index or a constraint or changed its row layout. | Dead. Nothing committed. | Roll back. Replay from `BEGIN`. |
| `CADB0504` `TransactionMustRetry` | A transient condition exhausted internal retries: start, admission, routing, leader transition, transport, lock-wait or replication-wait deadline, storage write conflict before the write, a read that waited too long for a commit, an unservable read range, or a schema leader change during DDL. | Dead. Nothing written. | Roll back. Replay from `BEGIN` with backoff. |
| `CADB0505` `TransactionLifetimeExceeded` | A serializable read-write transaction passed its lifetime cap (1 hour by default). | Dead. | Roll back. Replay from `BEGIN`. Shorten it. |
| `CADB0509` `TransactionFinalizeUnresolved` | A `COMMIT` or `ROLLBACK` has no final answer after the server's own retries. | Unknown. It may have committed. | The driver's commit or rollback method resends the same finalize, with bounded retries. If the error still reaches you, send the same finalize again, on the same transaction id. Never replay the callback. |
| `CADB0506` `TransactionMutationLimitExceeded` | The transaction would pass the mutation cap. | Rejected before storage. | Do not retry. Split the work. |
| `CADB0503` `SchemaCatchingUp` | The node is more than one schema version behind. | Rejected. | Not in the helpers' automatic set. Retry after the node catches up, or on another node. |
| `CADB0555` `LockNotAvailable` | `SELECT ... FOR UPDATE NOWAIT` met a held row. | Dead. | Roll back. Do not replay automatically: `NOWAIT` asked not to wait. Let the application decide. |
| `CADB0558` `ConcurrencyLimitExceeded` | `BEGIN` passed `max_open_explicit_transactions` on the node. | Not started. | Retry after a back-off. |
| `CADB0824` `TransactionRollbackOnly` | A statement, or the commit, ran after an earlier statement failed. | Dead. A commit rolled it back. | Roll back. Fix the code that swallowed the error. Handle the error that the message names. |
| `CADB0400` "Unknown transaction" | HTTP or gRPC: a handle used after a failed statement, after the idle timeout, or on another node. | Gone. | Start a new transaction. Fix the code if it swallowed an error. |

The server raises `CADB0509` after `transaction_finalize_retry_budget_ms`
(15 seconds by default) of same-handle retries. Four conditions cause it: a
leadership change during the finalize, a drain, a participant that shed a write
under load, or a transport fault after the commit request left the node.

After a finalize starts, CamusDB accepts no further data statement on that
transaction.

The schema can change during a transaction. If a written table gains an index
or a constraint before the commit, CamusDB aborts the transaction with
`CADB0502`. A table that was read or written and changed its row layout also gives
`CADB0502`, at the commit or at the next statement on that table. Replay it.

## A failed statement aborts the transaction

Any failed statement in an explicit transaction aborts it, whether or not it wrote
anything. So do a cancelled statement (`CADB0554`) and a statement refused inside
a transaction block (`CADB0538`). There are no savepoints. A client cannot tell
from the error whether the statement staged a write, so one rule covers all cases.

- The engine refuses each later statement, reads included, with `CADB0824`.
- `COMMIT` rolls back and reports `CADB0824`. Nothing is stored.
- `ROLLBACK` ends the transaction.
- The HTTP and gRPC transports roll back as soon as the statement fails. The client
  sees the failure once. A later statement or `COMMIT` gets `CADB0400` "Unknown
  transaction". A `ROLLBACK` still succeeds.
- The gRPC `COMMIT_IF_OK` answers with the first failure's code instead.

Never catch a statement error in the body and go on. This code worked in other
databases, and it never commits here:

```text
BEGIN
try:   INSERT INTO users (id, email) VALUES (...)
catch duplicate key: pass          # the transaction is already dead
UPDATE users SET last_seen = ...   # refused
COMMIT                             # refused, nothing is stored
```

Choose one of these instead:

- Prevent the expected error in SQL, with `INSERT ... ON CONFLICT` or `UPSERT INTO`.
- Let the error reach the retry helper or loop. It rolls back, then replays or raises.
- To run a fallback, roll back first. Then do the fallback in a new transaction.

## Backoff

The .NET and TypeScript helpers use this schedule (the Python helper also uses
bounded exponential backoff with jitter):

```text
delay = min(20 ms * 2^attempt, 400 ms), plus or minus 25 percent jitter
default maximum attempts = 5 (the first attempt included)
```

Jitter stops conflicting clients from retrying in step. Always back off on
`CADB0504`: it can mean that the node is shedding load at admission.

## .NET driver

Package namespace: `CamusDB.Client`. The exception type is `CamusException`,
with a `Code` property.

The recommended form: keep the transaction inside the callback, so each attempt
starts fresh. Disposal rolls back unfinished work.

```csharp
using CamusDB.Client;

await SerializableRetryHelper.ExecuteAutocommitAsync(async ct =>
{
    await using var tx = await connection.BeginTransactionAsync(ct);
    await using var command = connection.CreateCamusCommand("""
        UPDATE products SET stock = stock - 1 WHERE name = @name AND stock > 0
        """);
    command.Transaction = tx;
    command.Parameters.Add("@name", ColumnType.String, "kettle");
    await command.ExecuteNonQueryAsync(ct);
    await tx.CommitAsync(ct);
}, maxAttempts: 5);
```

For a single autocommit statement, the callback can run the command without an
explicit transaction. A generic overload
`ExecuteAutocommitAsync<T>(Func<CancellationToken, Task<T>>, int maxAttempts = 5,
CancellationToken)` returns a value.

If you own the loop, use `IsRetryable` and replay every read and write:

```csharp
const int MaxAttempts = 5;

for (int attempt = 1; ; attempt++)
{
    CamusTransaction tx = await connection.BeginTransactionAsync();
    try
    {
        // Do not catch CamusException in these calls. A failed statement ends tx.
        long balance = await ReadBalance(tx, accountId);
        if (balance < amount)
            throw new InvalidOperationException("Insufficient funds");

        await Debit(tx, accountId, balance - amount);
        await tx.CommitAsync();
        break;
    }
    catch (CamusException ex) when (SerializableRetryHelper.IsRetryable(ex))
    {
        await tx.RollbackAsync();
        if (attempt >= MaxAttempts)
            throw;

        await Task.Delay(20 * (1 << attempt));
    }
    catch
    {
        await tx.RollbackAsync();
        throw;
    }
}
```

Notes:

- `IsRetryable` returns true for `CADB0502`, `CADB0504`, and `CADB0505`. It
  always returns false for `CADB0509`. In the driver source it also matches the
  message markers `MustRetry`, `AlreadyLocked`, and `commit returned Aborted`.
  The TypeScript `isRetryable` uses the same codes and markers.
- `CADB0555`, `CADB0824`, and "Unknown transaction" are not in the code set.
- An overload `IsRetryable(Exception?)` walks the inner-exception chain. The EF
  Core provider uses it.
- `CommitAsync` and `RollbackAsync` resend the same finalize on `CADB0509`, with
  backoff, before they throw it (driver source, up to 10 extra attempts).
- Set options per transaction with `CamusTransactionOptions`: `IsolationLevel`,
  `Mode`, `Locking`, `Affinity`. Use `CamusTransactionOptions.Snapshot` for a
  serializable read-only snapshot.
- Connection-string defaults: `IsolationLevel`, `TransactionMode`, `Locking`.
  Precedence: per-transaction options, then the connection string, then the
  server default.
- Transaction commands are pinned to the transaction endpoint, even with a
  multi-endpoint `Endpoint` list.

Docs: https://camusdb.github.io/docs/dotnet-driver

## TypeScript connector

Package: `camusdb`. Helpers exported: `CamusClient`, `withRetry`,
`isRetryable`, `computeDelayMs`, `OPTIMISTIC_TRANSACTION_OPTIONS`,
`SNAPSHOT_TRANSACTION_OPTIONS`.

The common case:

```ts
await client.transaction(async (txn) => {
  await client.execute(
    'UPDATE accounts SET balance = balance - @amount WHERE id = @id',
    { amount, id: from },
    { transaction: txn },
  );

  await client.execute(
    'UPDATE accounts SET balance = balance + @amount WHERE id = @id',
    { amount, id: to },
    { transaction: txn },
  );
});
```

- `transaction(work, options?)` commits when `work` returns and rolls back when
  it throws. On a retryable error it runs `work` again on a fresh transaction.
- In the connector source, `options` accepts `maxAttempts` (default 5),
  `transactionOptions` (`isolationLevel`, `mode`, `locking`, `affinity`), and
  `signal` (an `AbortSignal`).
- `work` must be safe to run more than once. Do not send email or charge a card
  inside it.
- Do not catch a statement error in `work` and return. The commit then fails with
  a code that is not retryable.

Your own unit of work:

```ts
import { withRetry } from 'camusdb';

await withRetry(async (attempt) => {
  await using txn = await client.beginTransaction();
  await client.execute(
    'UPDATE robots SET year = @year WHERE id = @id',
    { year: 1984, id },
    { transaction: txn },
  );
  await txn.commit();
}, { maxAttempts: 5 });
```

- `withRetry(operation, { maxAttempts, signal })` retries only while
  `isRetryable(error)` is true.
- Wrap a whole unit from `BEGIN` to `COMMIT`. Never wrap a commit alone.
- `txn.commit()` and `txn.rollback()` resend the same finalize on `CADB0509`.

Docs: https://camusdb.github.io/docs/typescript-connector

## Python driver

Package: `camusdb` (`camusdb-py`). Start with no transaction open. The helper
begins and commits each attempt, and rolls back on failure:

```python
import camusdb


def reserve_kettle(conn):
    return conn.execute(
        "UPDATE products SET stock = stock - 1 WHERE name = @name AND stock > 0",
        {"name": "kettle"},
    ).rowcount


with camusdb.connect(
    endpoint="http://localhost:5095", database="shop", autocommit=True,
) as conn:
    changed = camusdb.run_transaction(conn, reserve_kettle, max_attempts=5)
```

- For asyncio, pass an async callback to
  `await camusdb.aio.run_transaction(conn, callback, max_attempts=5)`.
- `CADB0502`, `CADB0504`, and `CADB0505` map to `SerializationFailure`, a
  subclass of `OperationalError`. `camusdb.is_retryable(exc)` supports a custom
  loop.
- `commit()` retries an unresolved finalize (`CADB0509`). It does not replay the
  callback.
- After a statement failure, the driver refuses more statements until
  `rollback()` clears the failed state. A commit of the failed transaction raises
  `InternalError`.

Docs: https://camusdb.github.io/docs/python-driver#transactions-and-retries

## Raw HTTP

Read this section only for a custom client. The drivers do these steps for you.

1. `POST /start-transaction` with `databaseName` and optional
   `isolationLevel`, `transactionMode`, `locking`, `priority`.
2. Read `txnIdPT` and `txnIdCounter` from the response.
3. Send each statement with those two fields, to the same node.
4. `POST /commit-transaction` or `POST /rollback-transaction` with the same
   ids.
5. On `CADB0509`, send the same commit or rollback request again.
6. After any other statement error, the server has already rolled back. The ids
   are dead. A `/rollback-transaction` is safe but not needed. Start again.

An error response looks like this:

```json
{ "status": "failed", "code": "CADB0502", "message": "..." }
```

A streaming query can fail after HTTP `200`. Read the code from the last
trailer line. A streaming autocommit query runs one attempt only.

`ROLLBACK` is idempotent by transaction id.

Docs: https://camusdb.github.io/docs/http-api

## gRPC

- Unary: `StartTransaction` returns a `TxnHandle`. Pass it to each call. End with
  `CommitTransaction` or `RollbackTransaction`.
- The error code travels in the `camus-error-code` trailer. A batch operation
  carries it in `BatchError`.
- A transaction started inside a `BatchExecute` stream belongs to that stream.
  CamusDB rolls it back if the stream ends without `COMMIT` or `ROLLBACK`.
- A failed operation in a transaction rolls it back. A later `COMMIT` on that
  handle fails. A `ROLLBACK` succeeds as a no-op.
- `COMMIT_IF_OK` commits only when no earlier operation of the transaction
  failed. Otherwise it answers with the first failure's code, so a conflict stays
  a conflict for your retry code. A short transaction can fit in one frame.

Docs: https://camusdb.github.io/docs/grpc-api and
https://camusdb.github.io/docs/sql-transactions#failed-statements

## Review checklist

- [ ] The loop restarts at `BEGIN`, not at the failed statement.
- [ ] The body catches no statement error and continues. There are no savepoints.
- [ ] An expected duplicate uses `ON CONFLICT`, not a caught `CADB0300`.
- [ ] `CADB0555` is not replayed automatically.
- [ ] Every read happens inside the loop, so the replay sees fresh data.
- [ ] The body has no external side effect, or the side effect is idempotent.
- [ ] `CADB0509` is not in the replay set.
- [ ] `CADB0506` is not retried. The code splits the work instead.
- [ ] Backoff with jitter is present, and attempts are bounded.
- [ ] Rollback runs on every failure path.
- [ ] All statements of one transaction go to one node. The drivers do this; a
      custom protocol client must do it itself.
- [ ] Application code uses the driver's retry helper, not a hand-written loop.
