# Retry patterns

Read this file when you write or review the retry code for a CamusDB
transaction. It gives the full contract, then one version per client.

## Contents

- The contract
- Backoff
- .NET driver
- TypeScript connector
- Raw HTTP
- gRPC
- Review checklist

## The contract

| Code | Meaning | What the transaction is | Client action |
| --- | --- | --- | --- |
| `CADB0502` `TransactionConflict` | A lock conflict or a conflicting concurrent write. | Dead. Nothing committed. | Roll back. Replay from `BEGIN`. |
| `CADB0504` `TransactionMustRetry` | A transient condition exhausted internal retries: start, admission, routing, leader transition, transport, lock-wait deadline, storage write conflict before the write, or an unservable read range. | Dead. Nothing written. | Roll back. Replay from `BEGIN` with backoff. |
| `CADB0505` `TransactionLifetimeExceeded` | A serializable read-write transaction passed its lifetime cap (1 hour by default). | Dead. | Roll back. Replay from `BEGIN`. Shorten it. |
| `CADB0509` `TransactionFinalizeUnresolved` | A `COMMIT` or `ROLLBACK` has no final answer after the server's own retries. | Unknown. It may have committed. | Send the same finalize again, on the same transaction id. |
| `CADB0506` `TransactionMutationLimitExceeded` | The transaction would pass the mutation cap. | Rejected before storage. | Do not retry. Split the work. |
| `CADB0503` `SchemaCatchingUp` | The node is more than one schema version behind. | Rejected. | Retry later, or on another node. |

The server raises `CADB0509` after `transaction_finalize_retry_budget_ms`
(15 seconds by default) of same-handle retries. Four conditions cause it: a
leadership change during the finalize, a drain, a participant that shed a write
under load, or a transport fault after the commit request left the node.

After a finalize starts, CamusDB accepts no further data statement on that
transaction.

The schema can change during a transaction. If a written table gains an index
or a constraint before the commit, CamusDB aborts the transaction and asks for a
replay. Treat that abort like the other replay codes.

## Backoff

Both driver helpers use the same schedule:

```text
delay = min(20 ms * 2^attempt, 400 ms), plus or minus 25 percent jitter
default maximum attempts = 5 (the first attempt included)
```

Jitter stops conflicting clients from retrying in step. Always back off on
`CADB0504`: it can mean that the node is shedding load at admission.

## .NET driver

Package namespace: `CamusDB.Client`. The exception type is `CamusException`,
with a `Code` property.

Single-statement work:

```csharp
await SerializableRetryHelper.ExecuteAutocommitAsync(async ct =>
{
    CamusTransaction tx = await connection.BeginTransactionAsync(ct);
    try
    {
        await using CamusCommand update = connection.CreateCamusCommand("""
            UPDATE robots SET price = @price WHERE name = @name
            """);

        update.Transaction = tx;
        update.Parameters.Add("@price", ColumnType.Float64, 99.0);
        update.Parameters.Add("@name", ColumnType.String, "T-800");

        await update.ExecuteNonQueryAsync(ct);
        await tx.CommitAsync(ct);
    }
    catch
    {
        await tx.RollbackAsync(ct);
        throw;
    }
}, maxAttempts: 5, cancellationToken);
```

A generic overload `ExecuteAutocommitAsync<T>(Func<CancellationToken, Task<T>>,
int maxAttempts = 5, CancellationToken)` returns a value.

A multi-statement transaction needs your own loop:

```csharp
const int MaxAttempts = 5;

for (int attempt = 1; ; attempt++)
{
    CamusTransaction tx = await connection.BeginTransactionAsync();
    try
    {
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

## Raw HTTP

1. `POST /start-transaction` with `databaseName` and optional
   `isolationLevel`, `transactionMode`, `locking`, `priority`.
2. Read `txnIdPT` and `txnIdCounter` from the response.
3. Send each statement with those two fields, to the same node.
4. `POST /commit-transaction` or `POST /rollback-transaction` with the same
   ids.
5. On `CADB0509`, send the same commit or rollback request again.

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
- `COMMIT_IF_OK` commits only when no earlier operation of the transaction
  failed. A short transaction can fit in one frame.

Docs: https://camusdb.github.io/docs/grpc-api

## Review checklist

- [ ] The loop restarts at `BEGIN`, not at the failed statement.
- [ ] Every read happens inside the loop, so the replay sees fresh data.
- [ ] The body has no external side effect, or the side effect is idempotent.
- [ ] `CADB0509` is not in the replay set.
- [ ] `CADB0506` is not retried. The code splits the work instead.
- [ ] Backoff with jitter is present, and attempts are bounded.
- [ ] Rollback runs on every failure path.
- [ ] All statements of one transaction go to one node.
