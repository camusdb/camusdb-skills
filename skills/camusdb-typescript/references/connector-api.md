# `camusdb` connector API

Read this file when you need an exact option, method, helper, or export of the Node.js connector.
Source docs: https://camusdb.github.io/docs/typescript-connector

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
- Administration helpers
- Other exports

## Install

```bash
npm install camusdb
npm install @grpc/grpc-js @grpc/proto-loader   # only for protocol: 'grpc'
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
| `tokenLifetimeSeconds` | `TokenLifetime` | `600` | Fallback lifetime when the server reports none. |
| `maxAutoPrepare` | `MaxAutoPrepare` | `128` | `0` disables auto-prepare. |
| `autoPrepareMinUsages` | `AutoPrepareMinUsages` | `2` | Executions before auto-prepare. |
| `channelPoolSize` | `ChannelPoolSize` | `2` | gRPC streams per endpoint. |
| `coalescingThreshold` | `CoalescingThreshold` | `10` | gRPC; `1` disables coalescing. |
| `coalescingDelayMs` | `CoalescingDelay` | `0` | gRPC coalescing delay in ms. |
| `requestFrames` | `RequestFrames` | `true` | gRPC; pack waiting operations into one frame. |
| `streamDrainTimeoutMs` | `StreamDrainTimeout` | `300000` | gRPC; how long a retired stream serves its transactions. |
| `backupEndpoint` | `BackupEndpoint` | `endpoint` | HTTP endpoint for backups. Required with gRPC. |
| `backupTimeoutSeconds` | `BackupTimeout` | `300` | Backup timeout. |
| `allowInsecureCredentials` | `AllowInsecureCredentials` | `false` | Allow credentials over remote plaintext. |
| `routingMode` | `RoutingMode` | `'auto'` | `'auto'`, `'learned'`, or `'off'`. |
| `routingNodes` | `RoutingNodes` | none | Map of server node id to endpoint URL. |
| `routingMaxHintAgeMs` | `RoutingMaxHintAge` | `5000` | Max learned-route age. |
| `defaultTransactionOptions` | `IsolationLevel`, `TransactionMode`, `Locking` | server defaults | Defaults for transactions and autocommit. |
| `int64` | `Int64` | `'auto'` | `'auto'`, `'number'`, or `'bigint'`. |

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
| `database`, `protocol`, `config` | getters |
| `toString()` | Masks secrets |
| `close()` / `[Symbol.asyncDispose]` | Releases nothing shared today |

## Statement options

`StatementOptions`: `transaction?`, `signal?` (`AbortSignal`), `timeoutSeconds?`, `int64?`.
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

`CamusQueryStream<T>`: `columns`, `columnKeys`, `for await` iteration, `toArray()`, `close()`, `await using`.

- Over REST, rows stream as the iterator advances.
- Over gRPC, the result is buffered, then replayed.
- A late serializable conflict can surface during iteration. Neither `query` nor `queryStream` retries a statement on its own.
- For a write with `RETURNING`, the stream sends no row before commit and reports no written-row count.
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
| `ARRAY(T)` | array of decoded elements |
| `NULL` | `null` |

## `camus` parameter helpers

| Helper | Binds |
| --- | --- |
| `camus.id(hex24)` | `OID`; rejects a UUID string |
| `camus.uuid(str)` | `UUID` from canonical hyphenated text |
| `camus.int64(number \| bigint)` | `INT64` |
| `camus.float64(n)` / `camus.float32(n)` | `FLOAT64` / `FLOAT32` |
| `camus.numeric(string \| number \| bigint)` | `NUMERIC`; prefer a string for exact text |
| `camus.bool(b)` / `camus.string(s)` | `BOOL` / `STRING` |
| `camus.bytes(Uint8Array \| ArrayBuffer)` | `BYTES` |
| `camus.vector(ArrayLike<number>)` | packed `float32` `BYTES` |
| `camus.date(Date)` / `camus.dateTime(Date)` | `DATE` / `DATETIME` |
| `camus.array(items, ColumnType.X)` | `ARRAY(X)`; needed for an empty array |
| `camus.null()` | typed `NULL` |
| `camus.raw(columnValue)` | a raw wire `ColumnValue` |

- `ARRAY(NUMERIC)` is not supported. The connector rejects it before sending.
- A bad parameter fails locally with `CADB0400`.
- Tick helpers: `ticksToDate`, `dateToTicks`, `dateToDayTicks`, `UNIX_EPOCH_TICKS`, `TICKS_PER_MILLISECOND`, `TICKS_PER_DAY`.
- UUID helpers: `isUuid`, `uuidToBytes`, `bytesToUuid`.
- `CamusObjectId.generate()`, `CamusObjectId.generateAsString()`, `CamusObjectId.parse(str)`. An ObjectId starts with a timestamp.
- `CamusVector.toBytes(...)`, `CamusVector.toFloats(bytes)`, `CamusVector.dimensions(bytes)`.

## Transactions

`CamusTransactionOptions`: `isolationLevel` (`'Serializable'`, `'ReadCommitted'`), `mode` (`'ReadWrite'`, `'ReadOnly'`), `locking` (`'Pessimistic'`, `'Optimistic'`), `affinity` (exact SQL text).
Constants: `CamusIsolationLevel`, `CamusTransactionMode`, `CamusLocking`.
Presets: `DEFAULT_TRANSACTION_OPTIONS`, `OPTIMISTIC_TRANSACTION_OPTIONS`, `SNAPSHOT_TRANSACTION_OPTIONS`.

`CamusTransaction` members: `commit(signal?)`, `rollback(signal?)`, `isStarted`, `isFinalized`, `txnIdPT` (`bigint`), `txnIdCounter`, `transactionId`, `endpoint`, `options`, `[Symbol.asyncDispose]`.

- `commit()` re-issues the same finalize on `CADB0509`.
- `await using` rolls back an unfinished transaction and swallows a rollback failure.
- With learned routing, `BEGIN` waits for the first statement unless `affinity` is set.

## Retries and errors

- `withRetry<T>(operation: (attempt: number) => Promise<T>, { maxAttempts = 5, signal }?)`.
- Backoff: 20 ms base, doubling, capped at 400 ms, with 25 percent jitter (`computeDelayMs(attempt)`).
- `isRetryable(error)` is true for `CADB0502`, `CADB0504`, `CADB0505`. It walks `cause` chains. It is false for `CADB0509`.
- In source, `isRetryable` also matches the message markers `MustRetry`, `AlreadyLocked`, and `commit returned Aborted`.
- `CamusError` has `code` and `message`. Use `CamusError.is(value)`; it works across package copies.

`CamusErrorCode` constants: `Generic` (`CADB0000`), `EndpointUnreachable` (`CADB0001`), `InvalidParameter` (`CADB0400`), `InvalidVector` (`CADB0410`), `ColumnStorageNotApplicable` (`CADB0414`), `NumericValueOutOfRange` (`CADB0417`), `DatabaseAlreadyExists` (`CADB0012`), `FinalizeUnresolved` (`CADB0509`), `AuthenticationFailed` (`CADB0516`), `InsufficientPrivilege` (`CADB0517`), `TooManyAuthAttempts` (`CADB0518`), `InsecureTransport` (`CADB0519`), `UnknownPreparedStatement` (`CADB0520`), `PreparedStatementLimitExceeded` (`CADB0521`), `StatementNotAllowedInTransaction` (`CADB0538`), `LargeValueCorrupt` (`CADB0540`), `LargeValueNotResolved` (`CADB0541`).

Compare other server codes as strings, for example `error.code === 'CADB0502'`.

## Prepared statements

- Auto-prepare runs on the second execution and keeps the 128 most recent registrations.
- `await client.prepare(sql)` skips the warm-up for a hot statement.
- Preparable: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `SHOW`. Others run inline.
- If the server refuses a registration, the statement keeps running inline.
- Parameters still bind by name. The connector maps names to positions.

## Authentication

- Pass `user` and `password`, or call `client.login(user, password)` at runtime.
- Tokens are shared across clients with the same identity, renewed before expiry, and refreshed once after a rejection.
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

## Administration helpers

- `setColumnStorageStatement(table, column, CamusColumnStorage.Plain)`, `rewriteStorageStatement(...)`.
- Sequences: `createSequenceStatement(name, { startWith, ifNotExists })`, `dropSequenceStatement(name, { ifExists })`, `nextValueExpression(name)`, `selectNextValueStatement(name)`.
- Cache: `cacheHint(name, { ttlMs, strict })`, `evictCacheStatement(name)`, `evictAllCacheStatement()`, `CamusCacheStatus`.
- Backups on `client.backups`: `takeFullBackup()`, `takeIncrementalBackup(parentBackupId)`, `takeCoordinatedBackup()`, `listBackups()`, `getChain(leafBackupId)`, `previewGarbageCollection()`, `collectGarbage()`. Node-wide and REST only.

## Other exports

- `CamusTransportPool.closeAll()` closes shared gRPC channels at shutdown.
- `parseConnectionString`, `redactConnectionString`.
- `ColumnType`, `columnTypeName`, `isColumnType`, `decodeValue`.
- SQL text helpers: `delimitIdentifier`, `sqlLiteral`, `validateIdentifier`, `validateBareName`, `validateSqlLiteral`.
- `formatHlc`, `CamusRoutingDisposition`, `ROUTING_ACCEPT_VERSION`.
