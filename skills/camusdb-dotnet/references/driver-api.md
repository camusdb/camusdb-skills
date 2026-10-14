# CamusDB.Client driver API

Read this file when you configure a connection string, tune routing or prepared statements, or need an exact method name.
Source docs: https://camusdb.github.io/docs/dotnet-driver

## Contents

- Connection-string keys
- Endpoint pools and learned routing
- Authentication
- `CamusConnection` members
- `CamusCommand` members
- `CamusDataReader` members
- `CamusTransaction` and options
- Prepared statements
- Query result cache
- Vectors
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
| `User` | none | Aliases: `UserId`, `Uid`, `Username`. |
| `Password` | none | Alias: `Pwd`. |
| `AccessToken` | none | Bearer token from elsewhere. The driver does not renew it. |
| `TokenLifetime` | `600` | Fallback token reuse seconds when the server reports no expiry. |
| `MaxAutoPrepare` | `128` | Max statements kept prepared. `0` disables auto-prepare. |
| `AutoPrepareMinUsages` | `2` | Executions before the driver prepares a statement. |
| `IsolationLevel` | server (`Serializable`) | `Serializable` or `ReadCommitted`. |
| `TransactionMode` | `ReadWrite` | `ReadWrite` or `ReadOnly`. |
| `Locking` | server (`Pessimistic`) | `Pessimistic` or `Optimistic`. |
| `ChannelPoolSize` | `2` | gRPC only. `BatchExecute` streams per endpoint. |
| `CoalescingThreshold` | `10` | gRPC only. `1` disables coalescing. |
| `CoalescingDelay` | `2` | gRPC only. Milliseconds. `0` disables coalescing. |
| `RequestFrames` | `true` | gRPC only. Packs waiting operations into stream frames. |
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
- It renews from the server-reported expiry. It replays once after an expired or rotated token.
- Permission errors (`CADB0517`) are not retried.
- Explicit login: `string token = await connection.LoginAsync(user, password);` and `await connection.LogoutAsync();`.
- An `AccessToken` is never renewed. Expect `CADB0516` after it expires.
- The driver refuses credentials to a remote `http://` endpoint unless `AllowInsecureCredentials=true`.

## CamusConnection members

| Member | Notes |
| --- | --- |
| `new CamusConnection(CamusConnectionStringBuilder)` | Constructor. |
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
| `CreateBranchDatabaseAsync(branchName:, sourceDatabaseName:, ifNotExists:)` | Copy-on-write branch. |
| `ShowBranchesAsync(string)` / `ShowAncestorsAsync(string)` | Return `IReadOnlyList<CamusBranchRow>`. |
| `EvictCacheAsync(string)` / `EvictAllCacheAsync()` | Query result cache eviction. |
| `LoginAsync(user, password)` / `LogoutAsync()` | Explicit authentication. |
| `Backups` | `CamusBackupClient` (REST only). |
| `DefaultTransactionOptions` | Default `CamusTransactionOptions`. |

`CreateDatabaseAsync` retries a small set of transient schema-allocation conflicts internally.

## CamusCommand members

| Member | Notes |
| --- | --- |
| `Parameters.Add(name, ColumnType, value)` | Scalar parameter. |
| `Parameters.Add(name, ColumnType elementType, value, isArray: true)` | Array parameter. |
| `Parameters.Clear()` | Reuse a command in a loop. |
| `Transaction` | Set to the `CamusTransaction`. |
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

## CamusDataReader members

- Standard getters: `GetString`, `GetBoolean`, `GetInt16`, `GetInt32`, `GetInt64`, `GetFloat`, `GetDouble`, `GetDecimal`, `GetGuid`, `GetDateTime`, `IsDBNull`, `GetOrdinal`.
- `GetFieldValue<T>` for `DateOnly`, `DateTimeOffset`, `byte[]`, `float`, `Guid`, and other supported types.
- `GetVector(int ordinal)` returns `float[]` from a packed `BYTES` column.
- `RecordsAffected` holds the written-row count of a `RETURNING` statement.
- `CacheMetadata` holds the cache verdict. The streaming reader exposes no cache metadata.

## CamusTransaction and options

`CamusTransactionOptions` (a record with `init` properties):

| Property | Values |
| --- | --- |
| `IsolationLevel` | `CamusIsolationLevel.Serializable`, `CamusIsolationLevel.ReadCommitted` |
| `Mode` | `CamusTransactionMode.ReadWrite`, `CamusTransactionMode.ReadOnly` |
| `Locking` | `CamusLocking.Pessimistic`, `CamusLocking.Optimistic` |
| `Affinity` | Exact SQL text whose learned route picks the endpoint |

Static presets: `CamusTransactionOptions.Default`, `CamusTransactionOptions.Optimistic`, `CamusTransactionOptions.Snapshot` (serializable read-only).

`CamusTransaction` members: `CommitAsync()`, `RollbackAsync()`, `IsStarted`, `TxnIdPT`, `TxnIdCounter`, `TransactionId`, `Options`.
In routed mode the ids are zero until the transaction starts.

Retry codes:

| Code | Name | Action |
| --- | --- | --- |
| `CADB0502` | `TransactionConflict` | Replay from `BEGIN`. |
| `CADB0504` | `TransactionMustRetry` | Replay from `BEGIN`. |
| `CADB0505` | `TransactionLifetimeExceeded` | Replay from `BEGIN`. |
| `CADB0503` | `SchemaCatchingUp` | Retry later or on another node. |
| `CADB0509` | `TransactionFinalizeUnresolved` | Re-issue the same commit or rollback. |
| `CADB0506` | `TransactionMutationLimitExceeded` | Do not retry. Split the work. |

`SerializableRetryHelper` API:

- `IsRetryable(CamusException)` and `IsRetryable(Exception?)` (walks inner exceptions).
- `ExecuteAutocommitAsync(Func<CancellationToken, Task>, int maxAttempts = 5, CancellationToken = default)`.
- `ExecuteAutocommitAsync<T>(Func<CancellationToken, Task<T>>, int maxAttempts = 5, CancellationToken = default)`.

`IsRetryable` returns false for `CADB0509`. In source it also matches the message markers `MustRetry`, `AlreadyLocked`, and `commit returned Aborted`.

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

## Prepared statements

- The driver auto-prepares a statement after `AutoPrepareMinUsages` executions (default 2).
- It keeps up to `MaxAutoPrepare` statements (default 128). `MaxAutoPrepare=0` turns it off.
- Call `PrepareAsync()` for a statement you know is hot. Then reuse the command with `Parameters.Clear()`.
- Preparable: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `SHOW`. Other statements run inline.
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
