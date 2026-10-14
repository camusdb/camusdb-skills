---
name: camusdb-typescript
description: Writes and reviews TypeScript or JavaScript (Node.js) code that uses CamusDB through the `camusdb` npm package, and code in any other language that calls the CamusDB HTTP API or gRPC API directly. Use when code imports CamusClient, camus, withRetry, isRetryable, CamusError, CamusErrorCode, ColumnType, or CamusTransportPool from 'camusdb', or calls endpoints such as /execute-sql-query, /execute-sql-non-query, /start-transaction, /login, or the CamusSql gRPC service. Covers client options and connection strings, query/execute/queryStream, typed parameters and result shapes, transaction(...) and withRetry(...), CADB error codes, prepared statements, bearer tokens, and curl examples.
---

# CamusDB for TypeScript and HTTP clients

CamusDB is a distributed NewSQL database. Its default isolation is Serializable.
A conflicting transaction aborts, and the client must replay it.
This skill helps you write correct Node.js code with the `camusdb` connector.
It also covers the raw REST/JSON API and the gRPC API for other languages.

- npm package: `camusdb` (ESM only, TypeScript declarations included).
- Node.js: `^20.19.0` or `>=22.12.0`.
- gRPC transport needs optional peers `@grpc/grpc-js` and `@grpc/proto-loader`.

## Workflow

1. Pick the path. Use `camusdb` in Node.js. Use the HTTP API from other languages or shell scripts.
2. Configure `endpoint` and `database`. Use the REST port unless you set `protocol: 'grpc'`.
3. Create the database explicitly with `client.createDatabase(name, { ifNotExists: true })`.
4. Bind every value as a named parameter (`@name`). Use `camus.*` helpers for OID, UUID, NUMERIC, and empty arrays.
5. Put read-write units of work in `client.transaction(...)`. Keep the body free of side effects.
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

- `execute()` with `RETURNING` reports only the count. Use `query()` to read the returned rows.
- A repeated projection name becomes `name_2`, `name_3`, and so on.
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
| `CADB0506` | Mutation limit (20,000) | Do not retry; split the work |
| `CADB0503` | Schema catching up | Retry later or on another node |
| `CADB0520` | Unknown prepared statement | Prepare again; replay once |
| `CADB0516` / `CADB0517` | Authentication failed / insufficient privilege | Fix credentials or grants |
| `CADB0001` | Endpoint unreachable (client side) | Safe to retry elsewhere |

## Raw HTTP API

Every other language can use REST with JSON. All bodies use camel case.

```bash
curl -s -X POST http://localhost:5095/execute-sql-query \
  -H 'Content-Type: application/json' \
  -d '{"databaseName":"app","sql":"SELECT id, name FROM robots WHERE year >= @year",
       "parameters":{"@year":{"type":2,"longValue":1970}}}'
```

- A `ColumnValue` carries a numeric `type` (`2` = int64, `3` = string, `1` = id, `11` = uuid, `12` = numeric).
- Query responses return `columns` plus positional `rows` arrays.
- Explicit transactions use `/start-transaction`, then pass `txnIdPT` and `txnIdCounter` on each request.
- With authentication on, `POST /login` returns a bearer token. Send `Authorization: Bearer <token>`.

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
- Using `query()` for a huge result. Use `queryStream()`; it is incremental over REST.
- Expecting `queryStream()` to save memory over gRPC. The gRPC transport buffers internally.
- Setting `protocol: 'grpc'` without installing `@grpc/grpc-js` and `@grpc/proto-loader`.
- Pointing a gRPC client at the REST port, or a backup call at the gRPC port. Set `backupEndpoint`.
- Sending credentials over remote `http://`. Use `https://`; the connector refuses otherwise.
- Calling `client.login()` against a server with authentication off. It fails.
- Reading HTTP query `rows` as objects keyed by column. Rows are positional arrays aligned to `columns`.
- Dropping `causalToken` between raw HTTP requests. In a cluster, echo it to read your own writes.
- Running `TRUNCATE` inside a transaction. The server rejects it (`CADB0538`).

## References

- [references/connector-api.md](references/connector-api.md): every `CamusClient` option, method, helper, and export.
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
