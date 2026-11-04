# CamusDB.Client driver API

Read this file when you configure a connection string, tune routing or prepared statements, or need an exact method name.
Source docs: https://camusdb.github.io/docs/dotnet-driver

## Contents

- Connection-string keys
- Endpoint pools and learned routing
- Authentication
- `CamusConnection` members
- Provider factory and data source
- `CamusCommand` members
- `CamusDataReader` members
- `CamusTransaction` and options
- Pipelined transactions
- Prepared statements
- Query result cache
- Vectors
- C# extensions
- OpenTelemetry
- Node stats
- Backups
- Low-level gRPC client (`CamusDB.Grpc.Client`)

## Connection-string keys

Keys are case-insensitive and trimmed. Repeating a key is an error.
Quote a value with `'` or `"` when it contains `;`, `=`, or edge spaces.

| Key | Default | Meaning |
| --- | --- | --- |
| `Endpoint` | required | Base URL. Comma-separated list makes a pool. |
| `Database` | required | Database name sent with every request. |
| `Timeout` | `10` | Request timeout in seconds. |
| `Protocol` | `rest` | `rest` or `grpc`. |
| `HttpVersion` | `1.1` | Highest HTTP version for REST calls: `1.1`, `2`, or `3`. Other values read as `1.1`. |
| `User` | none | Aliases: `UserId`, `Uid`, `Username`. |
| `Password` | none | Alias: `Pwd`. |
| `AccessToken` | none | Bearer token from elsewhere. The driver does not renew it. |
| `TokenLifetime` | `600` | Fallback token reuse seconds when the server reports no expiry. The driver renews at 80 percent of it. |
| `MaxAutoPrepare` | `128` | Max statements kept prepared. `0` disables auto-prepare. |
| `AutoPrepareMinUsages` | `2` | Executions before the driver prepares a statement. |
| `IsolationLevel` | server (`Serializable`) | `Serializable` or `ReadCommitted`. |
| `TransactionMode` | `ReadWrite` | `ReadWrite` or `ReadOnly`. |
| `Locking` | server (`Pessimistic`) | `Pessimistic` or `Optimistic`. |
| `ChannelPoolSize` | `2` | gRPC only. `BatchExecute` streams per endpoint. |
| `CoalescingThreshold` | `10` | gRPC only. `1` disables coalescing. |
| `CoalescingDelay` | `0` | gRPC only. Milliseconds the pump waits after a multi-operation drain. `0` turns the wait off. |
| `RequestFrames` | `true` | gRPC only. Packs waiting operations into stream frames. Also gates pipelined transactions. |
| `TraceQueryText` | `false` | `false`, `true`, or `Sanitized`. Adds the SQL text to spans as `db.query.text`. |
| `BackupEndpoint` | `Endpoint` | HTTP endpoint for backups. Required with `Protocol=grpc`. |
| `BackupTimeout` | `300` | Backup timeout in seconds. |
| `AllowInsecureCredentials` | `false` | Allows credentials over a remote plaintext endpoint. |
| `RoutingMode` | `Auto` | `Auto`, `Learned`, or `Off`. |
| `RoutingNodes` | none | Trust map `nodeId=address,...`. Quote the value. |
| `RoutingMaxHintAge` | `5000` | Max learned-route age in milliseconds. |

Tune the gRPC batch keys only for a measured workload.

`CamusConnectionStringBuilder` helpers:

- `ToRedactedString()` and static `Redact(string)` mask `Password`, `Pwd`, and `AccessToken`.
- `PreparedStatementCount` and `IsPrepared(sql)` report auto-prepare state.
- A `CamusConnection` rejects a later change of its connection string (`NotSupportedException`).

## Endpoint pools and learned routing

```text
Endpoint=http://a:5095,http://b:5095,http://c:5095;Database=app;RoutingMode=Learned;RoutingNodes='camus-a:7070=http://a:5095,camus-b:7070=http://b:5095,camus-c:7070=http://c:5095'
```

- The pool rotates. An unreachable endpoint is skipped for 30 seconds.
- Rotation and health state are shared by connections with the same `Endpoint` value.
- Learned routing sends repeated statements to the node that leads their data. It is a latency optimization only.
- `Auto` negotiates only when `RoutingNodes` names at least two distinct endpoints. `Learned` needs one mapping.
- The driver only dials addresses listed in both `RoutingNodes` and `Endpoint`.
- Routes are learned per database, exact SQL text, and statement kind. Parameterized SQL benefits most.
- Advice covers simple single-table statements. Joins, views, DDL, `SHOW`, cache-hinted queries, and key-range sharded deployments get no advice.
- `CamusCommand.LastRoutingAdvice` exposes the last advice for diagnostics.
- With routing on, `BeginTransactionAsync()` can defer `BEGIN` to the first statement's endpoint. Set `CamusTransactionOptions.Affinity` to the exact SQL text of a hotter later statement to choose its endpoint.
- A transaction stays pinned to the endpoint where it started.

## Authentication

Authentication is off by default. With it on:

```csharp
CamusConnectionStringBuilder builder = new(
    "Endpoint=https://db.example.com:7141;Database=app;User=myapp;Password=app-secret");
```

- The driver exchanges the password for a short-lived bearer token (`/login` on REST, `CamusAuth` on gRPC).
- At 80 percent of the token lifetime, it starts one background login and keeps sending the current token.
- A statement waits for a login only with no token, or when the token reached 95 percent of its lifetime.
- A failed background renewal is tried again after 5 seconds.
- It replays a statement once after a rejected token (`CADB0516`).
- A login refused as busy (`CADB0518`) is sent again up to three times, after about 250 ms, 750 ms, and 1.5 s.
- With gRPC, a renewal rotates the streams. Transactions finish on their old stream, which closes after five minutes at most.
- Permission errors (`CADB0517`) are not retried.
- Explicit login: `string token = await connection.LoginAsync(user, password);` and `await connection.LogoutAsync();`.
- An `AccessToken` is never renewed. Expect `CADB0516` after it expires.
- The driver refuses credentials to a remote `http://` endpoint unless `AllowInsecureCredentials=true`.

## CamusConnection members

| Member | Notes |
| --- | --- |
| `new CamusConnection(CamusConnectionStringBuilder)` | Constructor. |
| `new CamusConnection()` | No connection string yet. Set `ConnectionString` once before use. |
| `OpenAsync()` / `Open()` | Opens. Creates no database. |
| `ChangeDatabase(string)` | Changes the target database. |
| `CreateCamusCommand(string sql)` | General SQL command. |
| `CreateSelectCommand(string sql)` | Query command. |
| `CreateInsertCommand(string table)` | Row insert helper (`CamusInsertCommand`). |
| `CreatePingCommand()` | Connectivity check with `ExecuteNonQueryAsync()`. |
| `BeginTransactionAsync()` / `BeginTransactionAsync(CamusTransactionOptions?)` | Returns `CamusTransaction`. |
| `CreateDatabaseAsync(bool ifNotExists = false)` | Uses the connection-string database. |
| `CreateDatabaseAsync(string name, bool ifNotExists = false)` | Explicit name. |
| `DropDatabaseAsync()` / `DropDatabaseAsync(string name)` | Drops a database. |
| `CreateBranchDatabaseAsync(branchName:, sourceDatabaseName:, ifNotExists:)` | Copy-on-write branch. Runs as SQL on both transports. Needs a superuser when authentication is on. |
| `ShowBranchesAsync(string)` / `ShowAncestorsAsync(string)` | Return `IReadOnlyList<CamusBranchRow>`. Run as SQL on both transports. Before 0.16.7, these REST calls failed with an HTTP 404. |
| `EvictCacheAsync(string)` / `EvictAllCacheAsync()` | Query result cache eviction. |
| `LoginAsync(user, password)` / `LogoutAsync()` | Explicit authentication. |
| `Backups` | `CamusBackupClient` (REST only). |
| `Endpoints` | The configured endpoint list. |
| `GetNodeStatsAsync()` / `GetNodeStatsAsync(string endpoint)` | Disk, CPU, and memory of one node. |
| `AccessToken` | The cached token, or `null`. |
| `DefaultTransactionOptions` | Default `CamusTransactionOptions`. |

`CreateDatabaseAsync` retries a small set of transient schema-allocation conflicts internally.

## Provider factory and data source

Use these with code that works with the abstract ADO.NET types, such as Testcontainers fixtures or health checks.

```csharp
DbProviderFactories.RegisterFactory(CamusProviderFactory.InvariantName, CamusProviderFactory.Instance);

await using CamusDataSource dataSource = new("Endpoint=http://localhost:5095;Database=test");
await using CamusConnection connection = dataSource.CreateConnection();
await connection.OpenAsync();

await using DbCommand command = dataSource.CreateCommand("SELECT name FROM robots");
```

- `CamusProviderFactory.InvariantName` is `"CamusDB.Client"`.
- `factory.CreateConnection()` returns a connection with no connection string. Set it once before use.
- A later change to a different connection string throws `NotSupportedException`.
- `factory.CreateConnectionStringBuilder()` returns `null`.
- `CamusDataSource` checks the connection string in its constructor.
- `ChangeDatabase` on one data-source connection does not change the others. They share one token and one transport.

## CamusCommand members

| Member | Notes |
| --- | --- |
| `Parameters.Add(name, ColumnType, value)` | Scalar parameter. |
| `Parameters.Add(name, ColumnType elementType, value, isArray: true)` | Array parameter. |
| `Parameters.Clear()` | Reuse a command in a loop. |
| `Transaction` | Set to the `CamusTransaction`. |
| `CommandType` | `Text` by default. `StoredProcedure` sends `CALL name(@a, @b, ...)` in parameter order. `TableDirect` throws. |
| `CommandTimeout` | Seconds. On a gRPC stream, it bounds each wait for the next row. |
| `ExecuteNonQueryAsync()` | Affected row count. |
| `ExecuteReaderAsync()` | Buffered `CamusDataReader`. |
| `ExecuteStreamReaderAsync()` | Streaming `CamusDataReader`. |
| `ExecuteScalarAsync()` | First cell. |
| `ExecuteDDLAsync()` | Schema statements; returns `bool`. |
| `PrepareAsync()` / `Prepare()` | Prepare a hot statement now. |
| `LastCacheMetadata` | Cache verdict of the last execution. |
| `LastRoutingAdvice` | Routing advice of the last execution. |

Parameters are input-only. `Cancel()` is cooperative through cancellation tokens.
On gRPC, a buffered `RETURNING` reply over the message limit fails with `CADB0550` before commit. Use `ExecuteStreamReaderAsync()` for it.

Execution notes:

- `INSERT`, `UPSERT`, `UPDATE`, and `DELETE` route as writes on every execute method. `UPSERT` has routed this way since 0.16.7.
- `CreateInsertCommand` has no upsert form. Use SQL.
- On gRPC, `ExecuteStreamReaderAsync()` uses the `ExecuteQuery` RPC on its own call. Rows arrive one message at a time.
- That gRPC path always runs the statement inline, also after `Prepare`.
- A buffered result over the server caps fails with `CADB0556`. Inline SQL over 8 MiB by default fails with `CADB0557`.
- `CADB0558` means the node reached its cap of open explicit transactions. Retry `BEGIN` after a backoff.

## CamusDataReader members

- Standard getters: `GetString`, `GetBoolean`, `GetInt16`, `GetInt32`, `GetInt64`, `GetFloat`, `GetDouble`, `GetDecimal`, `GetGuid`, `GetDateTime`, `IsDBNull`, `GetOrdinal`.
- `GetFieldValue<T>` for `DateOnly`, `DateTimeOffset`, `byte[]`, `float`, `Guid`, and other supported types.
- `GetFieldValue<T>` also parses a `JSONB` cell into `JsonDocument`, `JsonElement`, `JsonNode`, `JsonObject`, or `JsonArray`.
- `GetVector(int ordinal)` returns `float[]` from a packed `BYTES` column.
- `RecordsAffected` holds the written-row count of a `RETURNING` statement. On the streaming path it is `-1`.
- `CacheMetadata` holds the cache verdict. The streaming reader exposes no cache metadata.

## CamusTransaction and options

`CamusTransactionOptions` (a record with `init` properties):

| Property | Values |
| --- | --- |
| `IsolationLevel` | `CamusIsolationLevel.Serializable`, `CamusIsolationLevel.ReadCommitted` |
| `Mode` | `CamusTransactionMode.ReadWrite`, `CamusTransactionMode.ReadOnly` |
| `Locking` | `CamusLocking.Pessimistic`, `CamusLocking.Optimistic` |
| `Affinity` | Exact SQL text whose learned route picks the endpoint |
| `DeferBegin` | `true` sends `BEGIN` with the first statement or first pipeline send |

Static presets: `CamusTransactionOptions.Default`, `CamusTransactionOptions.Optimistic`, `CamusTransactionOptions.Snapshot` (serializable read-only).

`CamusTransaction` members: `CommitAsync()`, `RollbackAsync()`, `CreatePipeline()`, `IsStarted`, `TxnIdPT`, `TxnIdCounter`, `TransactionId`, `Options`.
A deferred transaction has zero ids until it starts.
Disposing a transaction before a commit or a rollback rolls it back. A deferred transaction that ran no statement sends nothing.

Retry codes:

| Code | Name | Action |
| --- | --- | --- |
| `CADB0502` | `TransactionConflict` | Replay from `BEGIN`. |
| `CADB0504` | `TransactionMustRetry` | Replay from `BEGIN`. |
| `CADB0505` | `TransactionLifetimeExceeded` | Replay from `BEGIN`. |
| `CADB0503` | `SchemaCatchingUp` | Retry later or on another node. |
| `CADB0509` | `TransactionFinalizeUnresolved` | Re-issue the same commit or rollback. |
| `CADB0506` | `TransactionMutationLimitExceeded` | Do not retry. Split the work. |
| `CADB0555` | `LockNotAvailable` | `FOR UPDATE NOWAIT` met a held row. The transaction ended. Not retried. Let the application decide. |
| `CADB0824` | `TransactionRollbackOnly` | The transaction is aborted. Roll back and start a new transaction. |
| `CADB0400` | `InvalidInput` | Message "Unknown transaction": a statement already failed and the server rolled back. Do not retry the statement. |

`SerializableRetryHelper` API:

- `IsRetryable(CamusException)` and `IsRetryable(Exception?)` (walks inner exceptions).
- `ExecuteAutocommitAsync(Func<CancellationToken, Task>, int maxAttempts = 5, CancellationToken = default)`.
- `ExecuteAutocommitAsync<T>(Func<CancellationToken, Task<T>>, int maxAttempts = 5, CancellationToken = default)`.

`IsRetryable` returns false for `CADB0509`. In source it also matches the message markers `MustRetry`, `AlreadyLocked`, and `commit returned Aborted`.
`CommitAsync()` and `RollbackAsync()` re-issue on `CADB0509` up to 10 times, from 50 ms doubling to about 3.2 s. After that, the code reaches you.

`CamusException` derives from `DbException`. Switch on its `Code` property, not on the message.
The client raises `CADB0001` (`CamusClientErrorCodes.EndpointUnreachable`) when the request never reached a server.

Manual loop for a multi-statement transaction:

```csharp
for (int attempt = 1; ; attempt++)
{
    CamusTransaction tx = await connection.BeginTransactionAsync();
    try
    {
        // every read and write of the unit, with command.Transaction = tx
        await tx.CommitAsync();
        break;
    }
    catch (CamusException ex) when (SerializableRetryHelper.IsRetryable(ex))
    {
        await tx.RollbackAsync();
        if (attempt >= 5) throw;
        await Task.Delay(20 * (1 << attempt));
    }
    catch
    {
        await tx.RollbackAsync();
        throw;
    }
}
```

Keep the catch blocks at this level. Do not catch a statement error inside the body and go on.
Any failed statement aborts the transaction, so the loop must see the original exception.
`RollbackAsync()` is safe after a failed statement or a failed commit. It succeeds and does nothing.

## Pipelined transactions

A pipeline sends statements with known inputs as one exchange. It saves round trips under high concurrency.

```csharp
CamusTransaction tx = await connection.BeginTransactionAsync(new CamusTransactionOptions
{
    Locking = CamusLocking.Optimistic,
    DeferBegin = true,
});
CamusPipeline pipeline = tx.CreatePipeline();

Task<CamusDataReader> balance = pipeline.QueueReader(read);
await pipeline.SendAsync();

Task<int> debited = pipeline.QueueNonQuery(debit);
await pipeline.CommitAsync(); // commits only if every statement of the transaction succeeded
```

- Do not set `command.Transaction` to another transaction. A command bound to another transaction is refused.
- `SendAsync` and `CommitAsync` throw the first failure. Each queued task carries its own result or exception.
- After a failure, statements later in that exchange fail too. `CommitAsync` does not commit.
- Replay the whole transaction from `BEGIN` to retry.
- `pipeline.Exchanges` reports the round trips. It is one per send over gRPC when the server announces frame version 2.
- Over REST, with `RequestFrames=false`, or on an older server, the statements run one exchange per step.
- DDL, `TRUNCATE`, and `CamusInsertCommand` cannot be queued.

## Prepared statements

- The driver auto-prepares a statement after `AutoPrepareMinUsages` executions (default 2).
- It keeps up to `MaxAutoPrepare` statements (default 128). `MaxAutoPrepare=0` turns it off.
- Call `PrepareAsync()` for a statement you know is hot. Then reuse the command with `Parameters.Clear()`.
- Preparable: `SELECT`, `INSERT`, `UPSERT`, `UPDATE`, `DELETE`, `SHOW`, and a query that starts with a parenthesis. Other statements run inline.
- The driver re-prepares and replays once on `CADB0520` (`UnknownPreparedStatement`).
- Behavior is unchanged: transaction, isolation, locks, affected rows, and result cache stay the same.
- REST handles live on one node for one principal. gRPC handles live on one `BatchExecute` stream.

## Query result cache

```csharp
string hint = CamusCacheHint.Build("recent_orders", ttl: TimeSpan.FromSeconds(30), strict: true);

await using CamusCommand select = connection.CreateSelectCommand(
    $"SELECT id, total FROM orders {hint} WHERE status = @status");
```

- Put the hint after the table reference.
- `CamusCacheMetadata` status values: `Hit`, `Miss`, `Bypass`, `StaleRevalidated`, `EvictedBeforePublish`.
- Evict with `connection.EvictCacheAsync(name)` or `EvictAllCacheAsync()`.

## Vectors

- Store a vector in a `BYTES` column as packed little-endian `float32`.
- Pack with `CamusVector.ToBytes(float[])`. Read with `reader.GetVector(ordinal)` or `CamusVector.ToFloats(bytes)`.
- Enforce the dimension with `CHECK (vector_dims(embedding) = 768)`.
- Rank with `l2_distance`, `cosine_distance` (ascending), or `inner_product` (descending), plus `LIMIT`.
- In EF Core, use `IsVector`. See [ef-core.md](ef-core.md).

## C# extensions

The server can run C# extension procedures, triggers, and functions. The driver needs no setup.

- Run a procedure with `CALL name(@a, @b)`, or set `CommandType.StoredProcedure` with the name as `CommandText`.
- `ExecuteNonQueryAsync()` returns the rows that the procedure's SQL changed. A procedure returns no result set.
- A procedure name must be a plain identifier. Any other text throws `InvalidOperationException`.
- `CALL` is not prepared. It needs a superuser in this server version.
- `CREATE TRIGGER` and `DROP TRIGGER` go to the DDL endpoint.
- Extension errors use `CADB08xx` codes. A conflict can replay a statement, so a trigger or a procedure can run more than once.

## OpenTelemetry

The driver emits through `ActivitySource` and `Meter`. It needs no OpenTelemetry package.

```csharp
builder.Services.AddOpenTelemetry()
    .WithTracing(tracing => tracing.AddSource(CamusTelemetry.ActivitySourceName))
    .WithMetrics(metrics => metrics.AddMeter(CamusTelemetry.MeterName));
```

- The optional `CamusDB.OpenTelemetry` package adds `AddCamusDBInstrumentation()` for both builders.
- The driver records one span for each command, typed insert, `BEGIN`, `COMMIT`, `ROLLBACK`, and pipeline exchange.
- The span name is `db.query.summary`, such as `SELECT orders customers`. With no table, it is `{operation} {database}`.
- `TraceQueryText=Sanitized` records `db.query.text` with each literal replaced by `?`. `TraceQueryText=true` records the text as sent.
- The driver never records parameter values.
- Tables come from a text scan, not a parse. A comma join names only its first table.
- Metrics include `db.client.operation.duration` and `camusdb.client.*` counters for logins, retries, quarantine, and prepared statements.

## Node stats

- `await connection.GetNodeStatsAsync()` reads the next endpoint in rotation. It works over REST and gRPC.
- `GetNodeStatsAsync(endpoint)` accepts only a value from `connection.Endpoints`.
- A value of `-1` means unknown. `Errors` names the field and the reason.
- `DataVolume.BelowWatermark` true means writes on that node fail with `CADB0536`.
- With authentication on, the account needs the superuser attribute or `MONITOR`.

## Backups

`connection.Backups` methods: `TakeFullBackupAsync()`, `TakeIncrementalBackupAsync(parentBackupId)`, `TakeCoordinatedBackupAsync()`, `GetChainAsync(leafBackupId)`, `ListBackupsAsync()`, `PreviewGarbageCollectionAsync()`, `CollectGarbageAsync()`.
Backups are node-wide and REST-only. The server needs `kahuna.backup_dir`.

## Low-level gRPC client (`CamusDB.Grpc.Client`)

The CamusDB source tree has a separate project, `CamusDB.Grpc.Client` (namespace `CamusDB.Grpc.Client`).
It has its own `CamusConnection` type. It is not the ADO.NET `CamusDB.Client.CamusConnection`.
Prefer `CamusDB.Client` with `Protocol=grpc` for application code.
The prepared-statements doc shows this low-level API:

```csharp
await using CamusConnection connection = CamusConnection.Connect("https://localhost:5001");

await using CamusPreparedStatement insert = await connection.PrepareAsync(
    "productiondb", "INSERT INTO robots (id, name, year) VALUES (gen_id(), @name, @year)");

await insert.ExecuteNonQueryAsync(["optimus", 1984L]);                     // by ordinal
await insert.ExecuteNonQueryAsync(new { name = "wall-e", year = 2008L });  // by name

CamusTransactionSession txn = await connection.BeginTransactionAsync("productiondb");
await txn.ExecuteNonQueryAsync(insert, ["bumblebee", 1985L]);
await txn.CommitAsync();
```

- Binding by name rejects a missing or an extra property. It never binds a silent `NULL`.
- The client re-registers statements on a rebuilt stream automatically.
- Options live in `CamusGrpcOptions` (`ChannelPoolSize`, `CoalescingThreshold`, `CoalescingDelayMs`, TLS settings).
