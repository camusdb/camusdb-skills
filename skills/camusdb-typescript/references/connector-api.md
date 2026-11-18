# `camusdb` connector API

Read this file when you need an exact option, method, helper, or export of the Node.js connector.
Source docs: https://camusdb.github.io/docs/typescript-connector, https://camusdb.github.io/docs/prepared-statements, and https://camusdb.github.io/docs/data-types#intervals

## Contents

- Install
- Client options and connection-string keys
- `CamusClient` members
- Statement options
- Result shapes
- `camus` parameter helpers
- Transactions
- Retries and errors
- Prepared statements
- Authentication
- Transport and routing
- Pipelined transactions
- C# extensions
- Node stats
- OpenTelemetry
- Client limits and hardening
- Administration helpers
- Other exports

## Install

```bash
npm install camusdb
npm install @grpc/grpc-js @grpc/proto-loader   # only for protocol: 'grpc'
npm install @opentelemetry/api                 # only for traces and metrics
```

The package is ESM only. Import with `import { ... } from 'camusdb'`.

## Client options and connection-string keys

`new CamusClient(options | connectionString)` or `CamusClient.fromConnectionString(str)`.

| Option | Connection-string key | Default | Meaning |
| --- | --- | --- | --- |
| `endpoint` | `Endpoint` | required | URL, array of URLs, or comma-separated list. |
| `database` | `Database` | required | Database sent with each request. |
| `timeoutSeconds` | `Timeout` | `10` | Request timeout. |
| `protocol` | `Protocol` | `'rest'` | `'rest'` or `'grpc'` (`CamusProtocol.Rest`, `CamusProtocol.Grpc`). |
| `user` | `User` (`UserId`, `Uid`, `Username`) | none | User to authenticate as. |
| `password` | `Password` (`Pwd`) | none | Password for `user`. |
| `accessToken` | `AccessToken` | none | Bearer token from elsewhere. |
| `tokenLifetimeSeconds` | `TokenLifetime` | `600` | Fallback lifetime when the server reports none. The driver renews at 80 percent of it. |
| `maxAutoPrepare` | `MaxAutoPrepare` | `128` | `0` disables auto-prepare. |
| `autoPrepareMinUsages` | `AutoPrepareMinUsages` | `2` | Executions before auto-prepare. |
| `channelPoolSize` | `ChannelPoolSize` | `2` | gRPC streams per endpoint. |
| `coalescingThreshold` | `CoalescingThreshold` | `10` | gRPC; `1` disables coalescing. |
| `coalescingDelayMs` | `CoalescingDelay` | `0` | gRPC coalescing delay in ms. |
| `requestFrames` | `RequestFrames` | `true` | gRPC; pack waiting operations into one frame. |
| `streamDrainTimeoutMs` | `StreamDrainTimeout` | `300000` | gRPC; how long a retired stream serves its transactions. |
| `httpVersion` | `HttpVersion` | `1.1` | Only `1.1` works. `2` and `3` throw a `CamusError` when the client is built. |
| `backupEndpoint` | `BackupEndpoint` | `endpoint` | HTTP endpoint for backups. Required with gRPC. |
| `backupTimeoutSeconds` | `BackupTimeout` | `300` | Backup timeout. |
| `allowInsecureCredentials` | `AllowInsecureCredentials` | `false` | Allow credentials over remote plaintext. |
| `routingMode` | `RoutingMode` | `'auto'` | `'auto'`, `'learned'`, or `'off'`. |
| `routingNodes` | `RoutingNodes` | none | Map of server node id to endpoint URL. |
| `routingMaxHintAgeMs` | `RoutingMaxHintAge` | `5000` | Max learned-route age. |
| `defaultTransactionOptions` | `IsolationLevel`, `TransactionMode`, `Locking` | server defaults | Defaults for transactions and autocommit. |
| `int64` | `Int64` | `'auto'` | `'auto'`, `'number'`, or `'bigint'`. |
| `jsonb` | `Jsonb` | `'parse'` | `'parse'` or `'text'`. |
| `traceQueryText` | `TraceQueryText` | `false` | `false`, `true`, or `'sanitized'`. Adds the SQL text to spans. |

Connection-string keys are case-insensitive. Repeating a key is an error.
Quote a value with `'` or `"` when it has `;` or edge spaces.

## `CamusClient` members

| Member | Signature / notes |
| --- | --- |
| `query<T>` | `(sql, parameters?, options?) => Promise<QueryResult<T>>` |
| `queryOne<T>` | `(sql, parameters?, options?) => Promise<T \| undefined>` |
| `scalar<T>` | `(sql, parameters?, options?) => Promise<T \| undefined>` |
| `queryStream<T>` | `(sql, parameters?, options?) => Promise<CamusQueryStream<T>>` |
| `execute` | `(sql, parameters?, options?) => Promise<ExecuteResult>`; DDL is routed to `executeDdl` |
| `executeDdl` | `(sql, options?) => Promise<boolean>` |
| `insert` | `(table, values, options?) => Promise<number>` |
| `call` | `(procedure, parameters?, options?) => Promise<ExecuteResult>` |
| `ping` | `({ signal?, timeoutSeconds? }?) => Promise<boolean>` |
| `beginTransaction` | `(options?: CamusTransactionOptions, signal?) => Promise<CamusTransaction>` |
| `transaction<T>` | `(work: (txn) => Promise<T>, { maxAttempts?, transactionOptions?, signal? }?) => Promise<T>` |
| `prepare` | `(sql, options?) => Promise<void>` |
| `isPrepared` | `(sql) => boolean` |
| `preparedStatementCount` | getter |
| `learnedRouteCount` | getter |
| `login` | `(user, password, signal?) => Promise<string>` |
| `logout` | `(signal?) => Promise<void>` |
| `accessToken` | getter |
| `createDatabase` | `(database = current, { ifNotExists?, signal? }?)`; retries transient allocation conflicts |
| `dropDatabase` | `(database = current, { signal? }?)` |
| `createBranchDatabase` | `(branchName, sourceDatabaseName, { ifNotExists?, signal? }?)` |
| `showBranches` / `showAncestors` | `(database = current) => Promise<CamusBranchRow[]>` |
| `changeDatabase` | `(database) => void` |
| `rewriteStorage` | `(table, { inline?, signal?, timeoutSeconds? }?)` runs `ALTER TABLE ... REWRITE STORAGE` |
| `nextSequenceValue` | `(name, options?) => Promise<bigint>` |
| `evictCache` / `evictAllCache` | Query result cache eviction |
| `backups` | `CamusBackupClient` (REST only) |
| `nodeStats` | `(endpoint?, ...) => Promise<CamusNodeStats>` |
| `endpoints` | getter; the configured endpoint list |
| `database`, `protocol`, `config` | getters |
| `toString()` | Masks secrets |
| `close()` / `[Symbol.asyncDispose]` | Releases nothing shared today |

## Statement options

`StatementOptions`: `transaction?`, `signal?` (`AbortSignal`), `timeoutSeconds?`, `int64?`, `jsonb?`.
`AutocommitStatementOptions` adds `transactionOptions?` for the autocommit transaction.

## Result shapes

```ts
interface QueryResult<T> {
  rows: T[];
  columns: CamusColumn[];      // { name, type: ColumnType, typeName }
  rowCount: number;
  affectedRows?: number;       // set for writes with RETURNING
  cacheMetadata?: CamusCacheMetadata;
  routingAdvice?: CamusRoutingAdvice;
}

interface ExecuteResult {
  affectedRows: number;
  routingAdvice?: CamusRoutingAdvice;
}
```

`CamusQueryStream<T>`: `columns`, `columnKeys`, `affectedRows`, `for await` iteration, `toArray()`, `close()`, `await using`.

- Rows are incremental on REST and on gRPC. On gRPC, this holds since 0.9.0.
- gRPC uses the `ExecuteQuery` RPC on its own call, outside the `BatchExecute` streams. The server waits when you stop reading.
- On gRPC, `timeoutSeconds` bounds each wait for the next row, not the whole read.
- On gRPC, the statement always runs inline, because a prepared handle belongs to the batch streams.
- On REST, the request timeout does not cover the rows of a stream.
- A late serializable conflict can surface during iteration. Neither `query` nor `queryStream` retries a statement on its own.
- For a write with `RETURNING`, the stream sends no row before commit and reports no written-row count.
- The server buffers a write with `RETURNING` before commit, so `CADB0556` applies to it. A plain read has no such cap.
- `affectedRows` is the changed-row count of a `CALL`, and `undefined` for a query.
- On gRPC, a buffered `RETURNING` reply over 4 MiB fails with `CADB0550` before commit. Use `queryStream()`.

Decoded values:

| Column type | JavaScript value |
| --- | --- |
| `OID`, `STRING` | `string` |
| `NUMERIC` | canonical decimal `string` |
| `INT64` | `number` or `bigint` (by `int64` mode) |
| `FLOAT32`, `FLOAT64` | `number` |
| `BOOL` | `boolean` |
| `BYTES` | `Uint8Array` |
| `DATE`, `DATETIME` | UTC `Date` |
| `UUID` | lowercase canonical `string` |
| `JSONB` | parsed value, with an unsafe integer as `bigint`; or JSON text with `jsonb: 'text'` |
| `ARRAY(T)` | array of decoded elements |
| `INTERVAL` | ISO-8601 `string`, such as `P1Y2M3DT4H5M6.5S`; zero is `PT0S`; mixed signs stay, as in `P-1DT2H` |
| `NULL` | `null` |

`INTERVAL` is an expression type only. A table column cannot hold it. To store one, cast it to `STRING`.
To pass a span as a parameter, bind a number and give it a unit: `INTERVAL @n DAY`.

Over REST, `Infinity`, `-Infinity`, and `NaN` in a float cell arrive as JSON strings, and the driver returns them as strings.
Over REST, a non-finite float parameter is written as JSON `null`. gRPC carries these values as floats.

## `camus` parameter helpers

| Helper | Binds |
| --- | --- |
| `camus.id(hex24)` | `OID`; rejects a UUID string |
| `camus.uuid(str)` | `UUID` from canonical hyphenated text |
| `camus.int64(number \| bigint)` | `INT64` |
| `camus.float64(n)` / `camus.float32(n)` | `FLOAT64` / `FLOAT32` |
| `camus.numeric(string \| number \| bigint)` | `NUMERIC`; prefer a string for exact text |
| `camus.jsonb(value)` | `JSONB`; a `string` is JSON text, any other value is serialized; `camus.jsonb(null)` is the JSON `null` document |
| `camus.bool(b)` / `camus.string(s)` | `BOOL` / `STRING` |
| `camus.bytes(Uint8Array \| ArrayBuffer)` | `BYTES` |
| `camus.vector(ArrayLike<number>)` | packed `float32` `BYTES` |
| `camus.date(Date)` / `camus.dateTime(Date)` | `DATE` / `DATETIME` |
| `camus.array(items, ColumnType.X)` | `ARRAY(X)`; needed for an empty array |
| `camus.null()` | typed `NULL` |
| `camus.raw(columnValue)` | a raw wire `ColumnValue` |

- `ARRAY(NUMERIC)` and `ARRAY(JSONB)` are not supported. The connector rejects them before sending.
- A plain object binds as `JSONB`. A `bigint` inside it keeps every digit.
- A bad parameter fails locally with `CADB0400`.
- Tick helpers: `ticksToDate`, `dateToTicks`, `dateToDayTicks`, `UNIX_EPOCH_TICKS`, `TICKS_PER_MILLISECOND`, `TICKS_PER_DAY`.
- UUID helpers: `isUuid`, `uuidToBytes`, `bytesToUuid`.
- `CamusObjectId.generate()`, `CamusObjectId.generateAsString()`, `CamusObjectId.parse(str)`. An ObjectId starts with a timestamp.
- `CamusVector.toBytes(...)`, `CamusVector.toFloats(bytes)`, `CamusVector.dimensions(bytes)`.

## Transactions

`CamusTransactionOptions`: `isolationLevel` (`'Serializable'`, `'ReadCommitted'`), `mode` (`'ReadWrite'`, `'ReadOnly'`), `locking` (`'Pessimistic'`, `'Optimistic'`), `affinity` (exact SQL text), `deferBegin` (`true` sends `BEGIN` with the first statement).
Constants: `CamusIsolationLevel`, `CamusTransactionMode`, `CamusLocking`.
Presets: `DEFAULT_TRANSACTION_OPTIONS`, `OPTIMISTIC_TRANSACTION_OPTIONS`, `SNAPSHOT_TRANSACTION_OPTIONS`.

`CamusTransaction` members: `commit(signal?)`, `rollback(signal?)`, `pipeline()`, `isStarted`, `isFinalized`, `txnIdPT` (`bigint`), `txnIdCounter`, `transactionId`, `endpoint`, `options`, `[Symbol.asyncDispose]`.

- `commit()` and `rollback()` re-issue the same finalize on `CADB0509`, up to 10 times with backoff. After that, the code reaches you.
- `await using` rolls back an unfinished transaction and swallows a rollback failure. A second dispose sends nothing.
- A deferred transaction that ran no statement sends nothing when it is disposed.
- With learned routing, `BEGIN` waits for the first statement unless `affinity` is set.
- Any failed statement ends the transaction on the server. A later statement or commit fails with `CADB0400`. `rollback()` succeeds and does nothing.

## Retries and errors

- `withRetry<T>(operation: (attempt: number) => Promise<T>, { maxAttempts = 5, signal }?)`.
- Backoff: 20 ms base, doubling, capped at 400 ms, with 25 percent jitter (`computeDelayMs(attempt)`).
- `isRetryable(error)` is true for `CADB0502`, `CADB0504`, `CADB0505`. It walks `cause` chains. It is false for `CADB0509`.
- In source, `isRetryable` also matches the message markers `MustRetry`, `AlreadyLocked`, and `commit returned Aborted`.
- `CamusError` has `code` and `message`. Use `CamusError.is(value)`; it works across package copies.

`CamusErrorCode` constants: `Generic` (`CADB0000`), `EndpointUnreachable` (`CADB0001`), `InvalidParameter` (`CADB0400`), `InvalidVector` (`CADB0410`), `ColumnStorageNotApplicable` (`CADB0414`), `NumericValueOutOfRange` (`CADB0417`), `InvalidJson` (`CADB0418`), `JsonNestingTooDeep` (`CADB0419`), `DatabaseAlreadyExists` (`CADB0012`), `FinalizeUnresolved` (`CADB0509`), `AuthenticationFailed` (`CADB0516`), `InsufficientPrivilege` (`CADB0517`), `TooManyAuthAttempts` (`CADB0518`), `InsecureTransport` (`CADB0519`), `UnknownPreparedStatement` (`CADB0520`), `PreparedStatementLimitExceeded` (`CADB0521`), `StatementNotAllowedInTransaction` (`CADB0538`), `LargeValueCorrupt` (`CADB0540`), `LargeValueNotResolved` (`CADB0541`).

Compare other server codes as strings, for example `error.code === 'CADB0502'`.
A server can also send `CADB0400`. The constant name `InvalidParameter` does not mean the driver raised it.

## Prepared statements

- Auto-prepare runs on the second execution and keeps the 128 most recent registrations.
- `await client.prepare(sql)` skips the warm-up for a hot statement.
- Preparable: `SELECT`, `INSERT`, `UPSERT`, `UPDATE`, `DELETE`, `SHOW`, and a query that starts with a parenthesis. Others run inline.
- If the server refuses a registration, the statement keeps running inline.
- Parameters still bind by name. The connector maps names to positions.
- A repeated `@name` binds one value. A parameter that matches no placeholder is an error, and so is a placeholder with no parameter. Neither becomes `NULL`.
- A handle belongs to the node that prepared it. Behind a load balancer, the connector prepares each statement once on each node it reaches.
- Preparing checks no privileges. The server checks them at each execution.

## Authentication

- Pass `user` and `password`, or call `client.login(user, password)` at runtime.
- Tokens are shared across clients with the same identity.
- At 80 percent of the token lifetime, the driver starts one background login and keeps sending the current token.
- A statement waits for a login only with no token, or when the token reached 95 percent of its lifetime.
- A failed background renewal is tried again after 5 seconds.
- A login refused with `CADB0518` is sent again up to three times, after about 250 ms, 750 ms, and 1.5 s.
- A rejected token (`CADB0516`) is discarded, and the statement replays once.
- Privilege errors are not retried.
- `logout()` revokes the token. A later statement logs in again with the configured credentials.

## Transport and routing

```ts
const client = new CamusClient({
  endpoint: ['http://a:5095', 'http://b:5095', 'http://c:5095'],
  database: 'app',
  routingMode: 'learned',
  routingNodes: {
    'camus-a:7070': 'http://a:5095',
    'camus-b:7070': 'http://b:5095',
    'camus-c:7070': 'http://c:5095',
  },
});
```

- The connector routes only to endpoints that `routingNodes` maps and `endpoint` lists.
- gRPC statements share long-lived `BatchExecute` streams and carry the causal token.
- On token rotation, new work moves to a fresh stream.

## Pipelined transactions

A pipeline sends statements with known inputs as one exchange. It saves round trips under high concurrency.

```ts
const txn = await client.beginTransaction({ locking: CamusLocking.Optimistic, deferBegin: true });
const pipeline = txn.pipeline();

const readA = pipeline.query<{ balance: number }>(selectSql, { id: a });
await pipeline.send();
const balance = (await readA).rows[0]!.balance;

pipeline.execute(updateSql, { v: balance - amount, id: a });
await pipeline.commit(); // commits only if every statement of the transaction succeeded
```

`CamusPipeline` members: `query(sql, params?, { int64?, jsonb? }?)`, `execute(sql, params?)`, `call(procedure, params?)`, `send(signal?)`, `commit(signal?)`, `transaction`, `size`, `exchanges`.

- `send` and `commit` reject with the first failure. Each statement's promise carries its own outcome.
- A queued promise that nobody awaits is not an unhandled rejection.
- After a failure, statements later in that exchange fail too. `commit` does not commit. Replay the transaction from `BEGIN`.
- After `pipeline.commit()`, `txn.rollback()` does nothing and `txn.commit()` throws. `client.transaction` does not commit again.
- `exchanges` is one per send over gRPC when the server announces frame version 2. Otherwise each step is one exchange.
- DDL and `TRUNCATE` throw at queue time, and the pipeline stays usable.
- Run one send at a time per pipeline.

## C# extensions

```ts
await client.call('transfer', { from: 'alice', to: 'bob', amount: 30 }); // CALL transfer(@from, @to, @amount)
```

- `call` sends the parameters in the object's key order, because a procedure takes arguments by position.
- A procedure name must be a plain identifier. Any other text throws `TypeError` before the call.
- A parameter name must be ASCII letters, digits, or `_`, with an optional `@`. Any other name throws `CADB0400` before the call (from 0.13.0).
- A key that is a whole number sorts first in JavaScript. Give parameters names.
- `query`, `queryOne`, `scalar`, `queryStream`, and a pipeline also accept `CALL`. The result has no rows.
- `CALL` is not prepared, and it needs a superuser in this server version.
- `CREATE TRIGGER` and `DROP TRIGGER` go to the DDL route.
- Extension errors use `CADB08xx` codes. A conflict can replay a statement, so a trigger or a procedure can run more than once.

## Node stats

```ts
const stats = await client.nodeStats();
console.log(stats.dataVolume?.availableBytes, stats.dataVolume?.belowWatermark);

for (const endpoint of client.endpoints) await client.nodeStats(endpoint);
```

- It works over REST (`GET /v1/admin/stats`) and gRPC (`GetNodeStats`).
- `nodeStats(endpoint)` accepts only a value from `client.endpoints`. Any other value throws `TypeError`.
- A value of `-1` means unknown. `errors` names the field and the reason.
- `belowWatermark` true means writes on that node fail with `CADB0536`.
- With authentication on, the account needs the superuser attribute or `MONITOR`.

## OpenTelemetry

The driver reports through `@opentelemetry/api`. Install it and register an SDK before the first database call. Without it, the driver reports nothing.

- The driver records one span for each statement, typed insert, `BEGIN`, `COMMIT`, `ROLLBACK`, and pipeline `BATCH`.
- The span name is `db.query.summary`, such as `SELECT orders customers`. With no table, it is `{operation} {database}`.
- `traceQueryText: 'sanitized'` records `db.query.text` with each literal replaced by `?`. `true` records the text as sent.
- The driver never records parameter values. `db.query.text` is cut at 4096 characters.
- Tables come from a text scan, not a parse. A comma join names only its first table.
- A `queryStream` span ends at the last row, at a failed read, or at `close`.
- Metrics include `db.client.operation.duration` and `camusdb.client.*` counters for logins, retries, quarantine, and prepared statements.

## Client limits and hardening

From 0.13.0:

- A REST response body is limited to 256 MiB. One line of a streamed result is limited to 64 MiB. A larger response throws a `CamusError`.
- The request timeout covers the whole response body on REST routes, login, and logout.
- The rows of `queryStream` are not held to the timeout, but an error body on that route is.
- A server-reported token lifetime is capped at 24 hours, so the driver always renews.
- A control character in a database name or an endpoint is refused on every entry point.

## Administration helpers

- `setColumnStorageStatement(table, column, CamusColumnStorage.Plain)`, `rewriteStorageStatement(...)`.
- Sequences: `createSequenceStatement(name, { startWith, ifNotExists })`, `dropSequenceStatement(name, { ifExists })`, `nextValueExpression(name)`, `selectNextValueStatement(name)`.
- Cache: `cacheHint(name, { ttlMs, strict })`, `evictCacheStatement(name)`, `evictAllCacheStatement()`, `CamusCacheStatus`.
- Backups on `client.backups`: `takeFullBackup()`, `takeIncrementalBackup(parentBackupId)`, `takeCoordinatedBackup()`, `listBackups()`, `getChain(leafBackupId)`, `previewGarbageCollection()`, `collectGarbage()`. Node-wide and REST only.

## Other exports

- `CamusTransportPool.closeAll()` closes shared gRPC channels at shutdown.
- `parseConnectionString`, `redactConnectionString`.
- `ColumnType`, `columnTypeName`, `isColumnType`, `decodeValue`.
- `formatJsonb(json)`, `JSONB_MAX_NESTING_DEPTH` (512).
- `CamusQueryTextMode`, `CAMUS_TELEMETRY_NAME`, `CamusDiskVolumeRole`.
- SQL text helpers: `delimitIdentifier`, `sqlLiteral`, `validateIdentifier`, `validateBareName`, `validateSqlLiteral`.
- `formatHlc`, `CamusRoutingDisposition`, `ROUTING_ACCEPT_VERSION`.
