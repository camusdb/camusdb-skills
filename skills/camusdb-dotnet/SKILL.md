---
name: camusdb-dotnet
description: Writes and reviews .NET (C#) code that talks to CamusDB through the CamusDB.Client ADO.NET driver, the CamusDB.Dapper adapter, or the CamusDB.EntityFrameworkCore provider. Use when code references CamusConnection, CamusCommand, CamusConnectionStringBuilder, CamusTransaction, SerializableRetryHelper, ColumnType, CamusValue, CamusDapper, UseCamusDB, EnableRetryOnFailure, or CADB error codes, or when a user asks how to connect a .NET, ASP.NET, Dapper, or EF Core app to CamusDB. Covers connection strings, parameters, type mapping (OID, UUID, NUMERIC, DATETIME, BYTES, ARRAY), transactions and serializable retries, endpoint pools and learned routing, prepared statements, streaming readers, migrations, and primary-key choice.
---

# CamusDB for .NET

CamusDB is a distributed NewSQL database. Its default isolation is Serializable.
A conflicting transaction aborts, and the client must replay it.
This skill helps you write correct .NET data-access code for CamusDB.
It covers three NuGet packages that share one connection string format.

| Package | Use it for | Targets |
| --- | --- | --- |
| `CamusDB.Client` | ADO.NET driver: full SQL control | `net8.0`, `net9.0`, `net10.0` |
| `CamusDB.Dapper` | Dapper mapping over `CamusConnection` | `net8.0`, `net9.0`, `net10.0` |
| `CamusDB.EntityFrameworkCore` | EF Core 10 provider, LINQ, migrations | `net10.0` |

## Workflow

Follow these steps when you write or review CamusDB .NET code.

1. Pick the package. Use EF Core for LINQ and migrations. Use Dapper for SQL plus object mapping. Use the driver for full control.
2. Build the connection string. Set `Endpoint` and `Database`. Add `Protocol=grpc` only with the gRPC port.
3. Create the database explicitly. Opening a connection creates nothing.
4. Choose primary keys with the placement rules in "Primary keys" below.
5. Bind every value as a parameter. Set the `ColumnType` for OID, UUID, NUMERIC, and arrays.
6. Wrap each read-write unit of work in a retry loop that replays from `BEGIN`.
7. Use a streaming reader for large results.
8. Check the "Common mistakes" list before you finish.

## Connect

```csharp
using CamusDB.Client;

CamusConnectionStringBuilder builder =
    new("Endpoint=http://localhost:5095;Database=app");

await using CamusConnection connection = new(builder);
await connection.OpenAsync();

await connection.CreateDatabaseAsync(ifNotExists: true);
```

- REST is the default transport. The REST port in the docs examples is `5095`.
- For gRPC, set `Protocol=grpc` and point `Endpoint` at the gRPC port (`5096` in the docs).
- A comma-separated `Endpoint` is a pool. The driver rotates and skips a dead endpoint for 30 seconds.
- Add `User` and `Password` when the server enables authentication. Use `https://` outside loopback.
- Log `builder.ToRedactedString()`, never `ToString()`. `ToString()` includes credentials.

Read [references/driver-api.md](references/driver-api.md) for every connection-string key.

## Commands and parameters

Placeholders use `@name`. Add parameters with `Parameters.Add(name, ColumnType, value)`.
The driver adds a missing leading `@` to the parameter name.

```csharp
await using CamusCommand insert = connection.CreateCamusCommand("""
    INSERT INTO robots (id, name, year, price)
    VALUES (GEN_ID(), @name, @year, @price)
    """);

insert.Parameters.Add("@name", ColumnType.String, "R2-D2");
insert.Parameters.Add("@year", ColumnType.Integer64, 1977);
insert.Parameters.Add("@price", ColumnType.Numeric, 25.50m);

int inserted = await insert.ExecuteNonQueryAsync();
```

| Call | Use it for |
| --- | --- |
| `CreateCamusCommand(sql)` + `ExecuteNonQueryAsync()` | `INSERT`, `UPDATE`, `DELETE`; returns affected rows |
| `CreateSelectCommand(sql)` + `ExecuteReaderAsync()` | `SELECT`, `SHOW`; buffers the whole result |
| `ExecuteStreamReaderAsync()` | Large `SELECT` or large `RETURNING`; reads rows incrementally |
| `ExecuteScalarAsync()` | First column of the first row |
| `ExecuteDDLAsync()` | `CREATE TABLE`, indexes, constraints, `TRUNCATE TABLE` |
| `CreateInsertCommand(table)` | Row insert without SQL text |

Use `ExecuteReaderAsync()` or `ExecuteScalarAsync()` to read rows from `RETURNING`.
`ExecuteNonQueryAsync()` discards `RETURNING` rows and returns the count only.

Arrays need `isArray: true`. Pass the element type explicitly:

```csharp
command.Parameters.Add("@tags", ColumnType.Integer64, new long[] { 1, 2, 3 }, isArray: true);
command.Parameters.Add("@none", ColumnType.String, Array.Empty<string>(), isArray: true);
```

Read [references/type-mapping.md](references/type-mapping.md) before you bind OID, UUID, NUMERIC, DATE, DATETIME, BYTES, or ARRAY values.

## Read rows

```csharp
await using CamusCommand select = connection.CreateSelectCommand(
    "SELECT id, name, year FROM robots WHERE year > @year");
select.Parameters.Add("@year", ColumnType.Integer64, 1900);

await using CamusDataReader reader = await select.ExecuteStreamReaderAsync();
while (await reader.ReadAsync())
{
    string id = reader.GetString(0);
    long year = reader.GetInt64(2);
}
```

- `ExecuteReaderAsync()` materializes the result first. Use it for small results.
- The streaming reader cannot replay a serializable conflict after rows arrive. Use the buffered reader when automatic retry matters more.
- `DATETIME` values come back with `DateTimeKind.Utc`.

## Transactions and retries

`CADB0502`, `CADB0504`, and `CADB0505` abort the transaction. Replay the whole unit of work from `BEGIN`.
`CADB0509` means the commit outcome is unknown. Never replay the work for it.
`CamusTransaction.CommitAsync()` and `RollbackAsync()` already re-issue the same finalize on `CADB0509`.

```csharp
await SerializableRetryHelper.ExecuteAutocommitAsync(async ct =>
{
    CamusTransaction tx = await connection.BeginTransactionAsync(ct);
    try
    {
        await using CamusCommand debit = connection.CreateCamusCommand(
            "UPDATE accounts SET balance = balance - @amount WHERE id = @id");
        debit.Transaction = tx;
        debit.Parameters.Add("@amount", ColumnType.Numeric, amount);
        debit.Parameters.Add("@id", ColumnType.Uuid, fromId);
        await debit.ExecuteNonQueryAsync(ct);

        await tx.CommitAsync(ct);
    }
    catch
    {
        await tx.RollbackAsync(ct);
        throw;
    }
}, maxAttempts: 5, cancellationToken);
```

- Set `command.Transaction = tx` on every command in the transaction.
- Use `SerializableRetryHelper.IsRetryable(ex)` when you write your own loop.
- The helper backoff is `min(20 ms * 2^attempt, 400 ms)` plus or minus 25 percent.
- Keep side effects out of the retried body. It can run more than once.
- Use `CamusTransactionOptions.Snapshot` for a consistent read-only view across statements.
- Do not retry `CADB0506` (mutation limit). Split the work below 20,000 mutations.

Transaction options precedence: per-transaction options, then connection-string defaults, then the server default.

```csharp
CamusTransaction tx = await connection.BeginTransactionAsync(new CamusTransactionOptions
{
    IsolationLevel = CamusIsolationLevel.Serializable,
    Mode = CamusTransactionMode.ReadWrite,
    Locking = CamusLocking.Optimistic,
});
```

## Dapper

```csharp
using CamusDB.Client;
using CamusDB.Dapper;
using Dapper;

CamusDapper.Register(); // once at startup

Robot robot = await connection.QuerySingleAsync<Robot>(
    "SELECT * FROM robots WHERE id = @id",
    new { id = CamusValue.Id("66f31f0a0c715b91d8c90311") });
```

- Wrap OID strings with `CamusValue.Id(...)`, UUIDs with `CamusValue.Uuid(...)`, wide decimals with `CamusValue.Numeric("...")`.
- `IN @values` expands a list as usual. Use `CamusValue.Array(...)` with `= ANY(@values)` for one native array value.
- Pass the `CamusTransaction` as `transaction: tx`.

Read [references/dapper.md](references/dapper.md) for array handlers and object ids.

## EF Core

```csharp
using CamusDB.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;

var options = new DbContextOptionsBuilder<AppDbContext>()
    .UseCamusDB("Endpoint=http://localhost:5095;Database=app", camus =>
    {
        camus.EnableRetryOnFailure();
    })
    .Options;
```

- Register with `UseCamusDB(connectionString)` or `UseCamusDB(camusConnection)`.
- Map a string ObjectId key with `.HasColumnType("id").ValueGeneratedOnAdd()`. A plain `Guid` maps to native `UUID`.
- `EnableRetryOnFailure()` retries `CADB0502`, `CADB0504`, and `CADB0505` only.
- `EnsureCreatedAsync()` creates the database when it is missing, then the tables.
- Migrations cannot change a column type in place, add or drop a primary key, or create computed columns.

Read [references/ef-core.md](references/ef-core.md) before you model entities, write migrations, or use LINQ features.

## Primary keys

Every table needs a primary key. CamusDB rejects a table without one (`CADB0400`).
Match the key to the data placement mode:

- **Hash routing (default):** a whole table lives on one partition. `OID DEFAULT (gen_id())` and `gen_uuid_v7()` keys are fine.
- **Key-range sharding with a high insert rate:** use a random key. Use `UUID PRIMARY KEY NOT NULL DEFAULT (gen_uuid_v4())`.

```sql
CREATE TABLE players (
  id UUID PRIMARY KEY NOT NULL DEFAULT (gen_uuid_v4()),
  name STRING(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT (now())
);
```

Under key-range sharding, these keys always increase and create a hotspot on the last range:

- `gen_id()` OIDs, `CamusObjectIdGenerator.Generate()`, and EF string keys mapped to `"id"`.
- `gen_uuid_v7()` and .NET `Guid.CreateVersion7()`.
- Sequences, identity-style counters, and a leading `DATE` or `DATETIME` column.

From .NET, `Guid.NewGuid()` produces a random version 4 UUID. Bind it with `ColumnType.Uuid`.
For a composite key, put a well-spread column such as `tenant_id` first, then the time column.

## Common mistakes

- Opening a connection and expecting the database to exist. Call `CreateDatabaseAsync(ifNotExists: true)` or run `CREATE DATABASE`.
- Pointing `Protocol=grpc` at the REST port. gRPC and REST listen on different ports.
- Retrying only the failed statement after `CADB0502`. Replay the whole transaction from a new `BEGIN`.
- Replaying the unit of work after `CADB0509`. Re-issue the same commit or rollback instead.
- Binding an ObjectId as `ColumnType.String`. Use `ColumnType.Id`, or `CamusValue.Id(...)` in Dapper.
- Storing UUIDs in `STRING` columns. Use a native `UUID` column and `ColumnType.Uuid`.
- Declaring a `Guid` EF property with `HasColumnType("id")`. ObjectIds are 12 bytes; use `string`.
- Reading a wide `NUMERIC` into `decimal`. Read it with `GetString()` or a `string` property.
- Using `ExecuteReaderAsync()` for a huge result. It buffers everything; use `ExecuteStreamReaderAsync()`.
- Using `ExecuteNonQueryAsync()` to read `RETURNING` rows. It returns only the count.
- Running `TRUNCATE TABLE` inside an explicit transaction. CamusDB rejects it (`CADB0538`).
- Forgetting `command.Transaction = tx`. The command then runs in autocommit on any endpoint.
- Leaving a transaction idle. CamusDB rolls it back after `transaction_idle_timeout_ms` (5 minutes by default).
- Writing to one global counter row from many transactions. Shard the counter across rows.
- Using `gen_id()` or v7 UUID keys on a high-insert table under key-range sharding.
- Logging `CamusConnectionStringBuilder.ToString()`. Use `ToRedactedString()`.
- Expecting session state. CamusDB has no session `SET`, no temporary tables, and no `LISTEN`.

## References

Read these files when the task needs the detail:

- [references/driver-api.md](references/driver-api.md): connection-string keys, connection and command API, routing, prepared statements, auth, backups, low-level gRPC client.
- [references/type-mapping.md](references/type-mapping.md): SQL type to `ColumnType`, .NET, EF Core, and Dapper mappings.
- [references/ef-core.md](references/ef-core.md): provider options, model mapping, LINQ translation, migrations, limits.
- [references/dapper.md](references/dapper.md): `CamusValue`, array handlers, transactions with Dapper.

Public docs:

- https://camusdb.github.io/docs/dotnet-driver
- https://camusdb.github.io/docs/dapper
- https://camusdb.github.io/docs/ef-core
- https://camusdb.github.io/docs/serializable-retries
- https://camusdb.github.io/docs/prepared-statements
- https://camusdb.github.io/docs/connection-model
- https://camusdb.github.io/docs/sql-routing-advice
- https://camusdb.github.io/docs/best-practices
- https://camusdb.github.io/docs/grpc-api
