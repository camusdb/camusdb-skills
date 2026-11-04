---
name: camusdb-typescript
description: Writes and reviews TypeScript or JavaScript (Node.js) code that uses CamusDB through the `camusdb` npm package, and code in any other language that calls the CamusDB HTTP API or gRPC API directly. Use when code imports CamusClient, camus, withRetry, isRetryable, CamusError, CamusErrorCode, ColumnType, CamusPipeline, or CamusTransportPool from 'camusdb', or calls endpoints such as /execute-sql-query, /execute-sql-non-query, /start-transaction, /login, or the CamusSql gRPC service, or hits CADB0824 or CADB0556. Covers client options and connection strings, query/execute/queryStream, typed parameters, JSONB, and result shapes, transaction(...), withRetry(...), failed statements, pipelines, call(...) for C# extension procedures, nodeStats, OpenTelemetry, CADB error codes, prepared statements, bearer tokens, request limits, and curl examples.
---

# CamusDB for TypeScript and HTTP clients

CamusDB is a distributed NewSQL database. Its default isolation is Serializable.
A conflicting transaction aborts, and the client must replay it.
This skill helps you write correct Node.js code with the `camusdb` connector.
It also covers the raw REST/JSON API and the gRPC API for other languages.

- npm package: `camusdb` (ESM only, TypeScript declarations included).
- Node.js: `^20.19.0` or `>=22.12.0`.
- gRPC transport needs optional peers `@grpc/grpc-js` and `@grpc/proto-loader`.
- Traces and metrics need the optional peer `@opentelemetry/api`.

## Workflow

1. Pick the path. Use `camusdb` in Node.js. Use the HTTP API from other languages or shell scripts.
2. Configure `endpoint` and `database`. Use the REST port unless you set `protocol: 'grpc'`.
3. Create the database explicitly with `client.createDatabase(name, { ifNotExists: true })`.
4. Bind every value as a named parameter (`@name`). Use `camus.*` helpers for OID, UUID, NUMERIC, JSONB text, and empty arrays.
5. Put read-write units of work in `client.transaction(...)`. Keep the body free of side effects, and let statement errors leave it.
6. Switch on `CamusError.code`, never on the message.
7. Use `queryStream()` for large results.
8. Check the "Common mistakes" list before you finish.

## Create a client

```ts
import { CamusClient } from 'camusdb';

const client = new CamusClient({
  endpoint: 'http://localhost:5095',
  database: 'app',
});

await client.createDatabase('app', { ifNotExists: true });
```

Or use the .NET-style connection string:

```ts
const client = CamusClient.fromConnectionString(
  'Endpoint=http://localhost:5095;Database=app;Timeout=30',
);
```

- A client is cheap and safe to share. Transports, tokens, and routes are shared process-wide.
- `endpoint` can be one URL, an array, or a comma-separated string. Dead endpoints are skipped for 30 seconds.
- Log `client.toString()` or `redactConnectionString(...)`. Both mask secrets.
- On shutdown with gRPC, call `await CamusTransportPool.closeAll()`.

Read [references/connector-api.md](references/connector-api.md) for every option.

## Query and execute

```ts
import { camus } from 'camusdb';

interface Robot { id: string; name: string; year: number }

const { rows, columns, rowCount } = await client.query<Robot>(
  'SELECT id, name, year FROM robots WHERE year = @year',
  { year: 1977 },
);

const first = await client.queryOne<Robot>('SELECT * FROM robots LIMIT 1');
const total = await client.scalar<number>('SELECT COUNT(*) FROM robots');

const { affectedRows } = await client.execute(
  'UPDATE robots SET year = @year WHERE id = @id',
  { year: 1984, id: camus.id('507f1f77bcf86cd799439011') },
);
```

| Method | Returns | Use it for |
| --- | --- | --- |
| `query<T>(sql, params?, options?)` | `{ rows, columns, rowCount, affectedRows?, cacheMetadata?, routingAdvice? }` | `SELECT`, `SHOW`, writes with `RETURNING` |
| `queryOne<T>(...)` | `T \| undefined` | First row |
| `scalar<T>(...)` | `T \| undefined` | First column of the first row |
| `queryStream<T>(...)` | `CamusQueryStream<T>` | Large results; `for await` iteration |
| `execute(sql, params?, options?)` | `{ affectedRows, routingAdvice? }` | `INSERT`, `UPDATE`, `DELETE` |
| `executeDdl(sql, options?)` | `boolean` | Schema statements |
| `insert(table, values, options?)` | `number` | Row-level insert endpoint |
| `call(procedure, params?, options?)` | `{ affectedRows, routingAdvice? }` | C# extension procedure |

- `execute()` with `RETURNING` reports only the count. Use `query()` to read the returned rows.
- A repeated projection name becomes `name_2`, `name_3`, and so on.
- `UPSERT INTO` and `INSERT ... ON CONFLICT` route as writes on every method since 0.11.0.
- `query()` holds the whole result. A server cap of 1,000,000 rows or 256 MiB gives `CADB0556`. A REST body over 256 MiB throws a `CamusError`.
- `queryStream()` is incremental on REST and on gRPC. See [references/connector-api.md](references/connector-api.md).
- Pass `{ transaction: txn }` in the options to run inside a transaction.

## Parameters and types

The leading `@` is optional in the parameter object. Inference rules:

| JavaScript value | Binds as |
| --- | --- |
| `string` | `STRING` |
| whole `number`, `bigint` | `INT64` |
| fractional `number` | `FLOAT64` |
| `boolean` | `BOOL` |
| `Date` | `DATETIME` |
| `Uint8Array`, `ArrayBuffer` | `BYTES` |
| `Float32Array` | `BYTES` (packed vector) |
| `CamusObjectId` | `OID` |
| plain object | `JSONB` |
| array | `ARRAY(T)` inferred from elements |
| `null`, `undefined` | `NULL` |

Use `camus` helpers when inference cannot express the type:

```ts
import { camus, ColumnType } from 'camusdb';

await client.query('SELECT * FROM events WHERE id = @id AND ref = @ref', {
  id: camus.id('507f1f77bcf86cd799439011'),
  ref: camus.uuid('550e8400-e29b-41d4-a716-446655440000'),
});

await client.execute('INSERT INTO invoices (id, total, tags) VALUES (GEN_ID(), @total, @tags)', {
  total: camus.numeric('19.99'),
  tags: camus.array([], ColumnType.String),
});
```

Result values:

- `OID` and `STRING` return `string`. `UUID` returns a lowercase canonical `string`.
- `INT64` returns `number` when safe, else `bigint` (`int64: 'auto'`). Set `int64: 'bigint'` for one type.
- `NUMERIC` returns canonical decimal text (`string`). It never goes through `number`.
- `DATE` and `DATETIME` return UTC `Date` objects (millisecond precision).
- `BYTES` returns `Uint8Array`. Unpack vectors with `CamusVector.toFloats(...)`.
- `JSONB` returns the parsed value, or the text form with `jsonb: 'text'`.

### JSONB

```ts
await client.execute('INSERT INTO events (id, payload) VALUES (GEN_ID(), @payload)', {
  payload: { kind: 'login', user: { id: 7 } },
});

const { rows } = await client.query(
  'SELECT payload FROM events WHERE payload @> @filter',
  { filter: { kind: 'login' } },
);

const exact = await client.scalar<string>('SELECT payload FROM events LIMIT 1', undefined, { jsonb: 'text' });
```

- Use `camus.jsonb(value)` for JSON text, a JSON array, a scalar, or the JSON `null` document.
- A plain `string` binds as `STRING`, and a plain array binds as `ARRAY`.
- An array of plain objects fails before the request. The server has no `ARRAY(JSONB)`. Use `camus.jsonb([...])`.
- `parse` (the default) rounds long fractions. Use `text` when every digit matters.
- Set `jsonb` on the client (`Jsonb=text`), on one statement, or on one pipelined query.
- In `text` mode, JSON `null` reads as `'null'` and SQL `NULL` as `null`. In `parse` mode both read as `null`.
- Invalid JSON fails with `CADB0418`. Nesting deeper than 512 levels fails with `CADB0419`.
- A driver before 0.12.0 reads every `jsonb` cell as `null`, with no error.

## Transactions and retries

```ts
await client.transaction(async (txn) => {
  await client.execute(
    'UPDATE accounts SET balance = balance - @amount WHERE id = @id',
    { amount: camus.numeric(amount), id: from },
    { transaction: txn },
  );
  await client.execute(
    'UPDATE accounts SET balance = balance + @amount WHERE id = @id',
    { amount: camus.numeric(amount), id: to },
    { transaction: txn },
  );
});
```

- `transaction(work, { maxAttempts?, transactionOptions?, signal? })` commits on return and rolls back on throw.
- It replays the whole function on `CADB0502`, `CADB0504`, or `CADB0505`. Default `maxAttempts` is 5.
- The function can run more than once. Do not send email or charge cards inside it.
- `CADB0509` is resolved inside `commit()` by re-issuing the same commit. It is never replayed.
- Use `beginTransaction(options?)` when the work spans several functions. Use `await using` so an unfinished transaction rolls back.
- Wrap your own idempotent work with `withRetry(async (attempt) => ..., { maxAttempts, signal })`.
- Use `isRetryable(error)` in a custom loop.
- Use `txn.pipeline()` to send several statements as one exchange. See [references/connector-api.md](references/connector-api.md).

### A failed statement ends the transaction

Any failed statement aborts an explicit transaction, reads included. There are no savepoints.
Earlier writes of that transaction cannot commit.

- Over REST and gRPC, the server rolls the transaction back as soon as the statement fails.
- A later statement or commit on that transaction fails with `CADB0400` ("Unknown transaction").
- `CADB0824` (`TransactionRollbackOnly`) is the engine code for this state. Treat both codes as "this transaction is gone".
- `txn.rollback()` after the failure is safe. It succeeds and does nothing.

Do not catch a statement error in a `transaction(...)` body and continue.
The commit then fails with `CADB0400`, which `isRetryable` rejects, so a swallowed `CADB0502` is never replayed.

```ts
await client.transaction(async (txn) => {
  // Wrong: the transaction is already gone when the catch runs. The commit fails.
  try {
    await client.execute(insertSql, params, { transaction: txn });
  } catch (error) {
    if (!CamusError.is(error) || error.code !== 'CADB0300') throw error;
  }
});

// Right: state the conflict rule in SQL, so the statement does not fail.
await client.transaction(async (txn) => {
  await client.execute(
    'INSERT INTO users (id, email) VALUES (@id, @email) ON CONFLICT (email) DO NOTHING',
    params,
    { transaction: txn },
  );
});
```

To continue after an expected failure, let the body throw. Then start a new transaction.

```ts
import { SNAPSHOT_TRANSACTION_OPTIONS } from 'camusdb';

await using txn = await client.beginTransaction(SNAPSHOT_TRANSACTION_OPTIONS);
const a = await client.query('SELECT SUM(balance) AS s FROM accounts', undefined, { transaction: txn });
await txn.commit();
```

## Error handling

```ts
import { CamusError, CamusErrorCode } from 'camusdb';

try {
  await client.execute('INSERT INTO robots (id, name) VALUES (GEN_ID(), @name)', { name });
} catch (error) {
  if (CamusError.is(error) && error.code === CamusErrorCode.AuthenticationFailed) {
    // re-authenticate
  }
  throw error;
}
```

| Code | Meaning | Action |
| --- | --- | --- |
| `CADB0502`, `CADB0504`, `CADB0505` | Conflict, must retry, lifetime exceeded | Replay from `BEGIN` |
| `CADB0509` | Finalize unresolved | Re-issue the same commit or rollback |
| `CADB0400` | Bad input, or "Unknown transaction" after a failed statement | Fix the input, or start a new transaction |
| `CADB0824` | Transaction aborted by a failed statement | Roll back; start a new transaction |
| `CADB0555` | `FOR UPDATE NOWAIT` met a held row | The transaction ended. Not retried; let the application decide |
| `CADB0506` | Mutation limit (20,000) | Do not retry; split the work |
| `CADB0503` | Schema catching up | Retry later or on another node |
| `CADB0520` | Unknown prepared statement | Prepare again; replay once |
| `CADB0516` / `CADB0517` | Authentication failed / insufficient privilege | Fix credentials or grants |
| `CADB0518` | Login refused: busy or rate limited | The driver retries three times first; then back off |
| `CADB0556` | Buffered result too large | Use `queryStream()` or page the result |
| `CADB0001` | Endpoint unreachable (client side) | Safe to retry elsewhere |

## Raw HTTP API

Every other language can use REST with JSON. All bodies use camel case.

```bash
curl -s -X POST http://localhost:5095/execute-sql-query \
  -H 'Content-Type: application/json' \
  -d '{"databaseName":"app","sql":"SELECT id, name FROM robots WHERE year >= @year",
       "parameters":{"@year":{"type":2,"longValue":1970}}}'
```

- A `ColumnValue` carries a numeric `type` (`2` = int64, `3` = string, `1` = id, `11` = uuid, `12` = numeric, `13` = jsonb).
- Query responses return `columns` plus positional `rows` arrays.
- Explicit transactions use `/start-transaction`, then pass `txnIdPT` and `txnIdCounter` on each request.
- With authentication on, `POST /login` returns a bearer token. Send `Authorization: Bearer <token>`.
- `/update` and `/delete` refuse a request with no filter (`CADB0400`, HTTP 400).
- A repeated key in the `values` of `/insert` or `/update` fails with `CADB0403`, HTTP 400.

Read [references/http-api.md](references/http-api.md) before you write any raw HTTP client.
Read [references/grpc-api.md](references/grpc-api.md) before you build a gRPC client from the proto.

## Common mistakes

- Binding an ObjectId as a plain string. A `string` infers `STRING`; use `camus.id(...)` or `CamusObjectId`.
- Binding a UUID as a plain string. Use `camus.uuid(...)`.
- Binding money as a JavaScript `number`. It binds as `INT64` or `FLOAT64`; use `camus.numeric('19.99')`.
- Doing arithmetic on `NUMERIC` results as numbers. They are decimal strings.
- Passing an empty array without `camus.array([], ColumnType.X)`. The element type is unknown.
- Forgetting `{ transaction: txn }`. The statement then runs in autocommit outside the transaction.
- Running non-idempotent side effects inside `client.transaction(...)`. The function can repeat.
- Wrapping only `txn.commit()` in `withRetry`. Retry the whole unit of work.
- Replaying work after `CADB0509`. The commit may already be durable.
- Catching a statement error inside `client.transaction(...)` and continuing. The transaction is already gone. Rethrow it.
- Using `query()` for a huge result. Use `queryStream()`; it is incremental on both transports.
- Expecting `queryStream()` to avoid `CADB0556` for a write with `RETURNING`. The server buffers that write before commit.
- Treating `timeoutSeconds` as a limit on a `queryStream()` read. It bounds each wait for a row on gRPC, and not the rows on REST.
- Binding an array of plain objects. Use `camus.jsonb([...])`.
- Binding `Infinity` or `NaN` over REST. The driver's JSON writer sends `null`. Use gRPC for these values.
- Setting `protocol: 'grpc'` without installing `@grpc/grpc-js` and `@grpc/proto-loader`.
- Pointing a gRPC client at the REST port, or a backup call at the gRPC port. Set `backupEndpoint`.
- Sending credentials over remote `http://`. Use `https://`; the connector refuses otherwise.
- Calling `client.login()` against a server with authentication off. It fails.
- Reading HTTP query `rows` as objects keyed by column. Rows are positional arrays aligned to `columns`.
- Dropping `causalToken` between raw HTTP requests. In a cluster, send it on the next autocommit query or `/start-transaction`.
- Sending `causalToken` on a statement of an explicit transaction. A transaction takes its floor once, at start.
- Running `TRUNCATE` inside a transaction. The server rejects it (`CADB0538`).

## References

- [references/connector-api.md](references/connector-api.md): every `CamusClient` option, method, helper, and export, plus pipelines, node stats, OpenTelemetry, extensions, and client limits.
- [references/http-api.md](references/http-api.md): REST endpoints, `ColumnValue` encoding, response shapes, transactions, prepared statements, tokens, curl examples.
- [references/grpc-api.md](references/grpc-api.md): gRPC services, `Value` encoding, `BatchExecute`, retry trailers.

Public docs:

- https://camusdb.github.io/docs/typescript-connector
- https://camusdb.github.io/docs/http-api
- https://camusdb.github.io/docs/grpc-api
- https://camusdb.github.io/docs/connection-model
- https://camusdb.github.io/docs/prepared-statements
- https://camusdb.github.io/docs/serializable-retries
- https://camusdb.github.io/docs/sql-authentication
- https://camusdb.github.io/docs/json
- https://camusdb.github.io/docs/sql-transactions
