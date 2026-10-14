# CamusDB.Dapper adapter

Read this file when you use Dapper with CamusDB.
Package: `CamusDB.Dapper` (depends on `CamusDB.Client` and Dapper). Namespace: `CamusDB.Dapper`.
Source docs: https://camusdb.github.io/docs/dapper

## Setup

```csharp
using CamusDB.Client;
using CamusDB.Dapper;
using Dapper;

CamusDapper.Register(); // safe to call more than once

await using CamusConnection connection = new(
    new CamusConnectionStringBuilder("Endpoint=http://localhost:5095;Database=app"));
await connection.OpenAsync();
```

`Register()` adds the type handler for `CamusObjectIdValue`.

## Queries and writes

Use the usual Dapper calls: `QueryAsync`, `QuerySingleAsync`, `QuerySingleOrDefaultAsync`, `ExecuteAsync`, `ExecuteScalarAsync`.

```csharp
int inserted = await connection.ExecuteAsync(
    "INSERT INTO robots (id, name, year) VALUES (GEN_ID(), @Name, @Year)",
    new[] { new { Name = "R2-D2", Year = 1977 }, new { Name = "T-800", Year = 1984 } });

long count = await connection.ExecuteScalarAsync<long>("SELECT COUNT(*) FROM robots");
```

## CamusValue helpers

| Helper | Use |
| --- | --- |
| `CamusValue.Id(string?)` / `CamusValue.Id(CamusObjectIdValue?)` | Bind an `OID`. |
| `CamusValue.Uuid(Guid?)` | Bind a native `UUID`. |
| `CamusValue.Numeric(decimal?)` / `CamusValue.Numeric(string?)` | Bind `NUMERIC`, including text wider than `decimal`. |
| `CamusValue.Array<T>(IEnumerable<T>?)` / `CamusValue.Array<T>(values, ColumnType elementType)` | Bind one native `ARRAY(T)`. Keeps the element type for empty arrays. |
| `CamusValue.Vector(ReadOnlySpan<float>)` | Pack a vector for a `BYTES` column. |

`DynamicParameters` accepts `CamusValue` too: `parameters.Add("id", CamusValue.Id(robotId));`.

## Arrays

- Default Dapper behavior expands `IN @names` from a list. That still works.
- For one native array value, write `= ANY(@years)` and pass `CamusValue.Array(new long[] { 1977, 1984 })`.
- `CamusDapper.RegisterArrayTypeHandler<long>()` makes `long[]` bind as one `ARRAY(INT64)` and read back into `long[]`.
- `CamusDapper.RegisterArrayTypeHandlers()` registers `long`, `int`, `double`, `bool`, `string`, and `Guid` arrays.
- After you register a handler for a type, `IN @values` no longer expands that type. Use `= ANY(@values)`.

## Object ids

```csharp
using CamusDB.Core.Util.ObjectIds;

CamusObjectIdValue id = CamusObjectIdGenerator.Generate();
await connection.ExecuteAsync("INSERT INTO events (id, name) VALUES (@id, @name)", new { id, name = "opened" });
```

If the model stores the id as `string`, wrap it with `CamusValue.Id(...)` for writes and filters.

## Transactions

```csharp
await using CamusTransaction tx = await connection.BeginTransactionAsync();
try
{
    await connection.ExecuteAsync(
        "INSERT INTO robots (id, name) VALUES (GEN_ID(), @name)",
        new { name = "R2-D2" }, transaction: tx);
    await tx.CommitAsync();
}
catch
{
    await tx.RollbackAsync();
    throw;
}
```

Wrap the whole block in `SerializableRetryHelper.ExecuteAutocommitAsync(...)` or your own loop. Replay from `BEGIN` on `CADB0502`, `CADB0504`, `CADB0505`.
