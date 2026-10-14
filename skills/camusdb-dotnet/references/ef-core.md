# CamusDB EF Core provider

Read this file when you configure a `DbContext`, map entities, write migrations, or check LINQ support.
Package: `CamusDB.EntityFrameworkCore` (targets `net10.0`, EF Core 10 relational).
Namespace: `CamusDB.EntityFrameworkCore`.
Source docs: https://camusdb.github.io/docs/ef-core

## Register the provider

```csharp
using CamusDB.Client;
using CamusDB.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;

var options = new DbContextOptionsBuilder<AppDbContext>()
    .UseCamusDB("Endpoint=http://localhost:5095;Database=app;Timeout=30", camus =>
    {
        camus.UseOptimisticLocking();
        camus.EnableRetryOnFailure();
    })
    .Options;
```

Overloads:

- `UseCamusDB(string connectionString, Action<CamusDBDbContextOptionsBuilder>? camusOptionsAction = null)`.
- `UseCamusDB(DbConnection connection, Action<CamusDBDbContextOptionsBuilder>? camusOptionsAction = null)`. The context does not own or dispose that connection.
- Both have generic `DbContextOptionsBuilder<TContext>` versions. You can also call them in `OnConfiguring`.

The connection string accepts every driver key. See [driver-api.md](driver-api.md).

## Provider options (`CamusDBDbContextOptionsBuilder`)

| Method | Effect |
| --- | --- |
| `UseTransactionDefaults(CamusTransactionOptions)` | Default isolation, mode, and locking for the context. |
| `UseOptimisticLocking()` | Shortcut for `Locking = Optimistic`. |
| `UsePessimisticLocking()` | Shortcut for `Locking = Pessimistic`. |
| `EnableRetryOnFailure(maxRetryCount: 15, maxRetryDelay: 1 s, retryDeadline: 5 s, medianFirstRetryDelay: 30 ms)` | Execution strategy for `CADB0502`, `CADB0504`, `CADB0505`. |
| `UseForeignKeyConstraints(bool enabled = true)` | `false` keeps EF relationships but emits no database foreign keys. |

Unset options fall back to the connection string, then to the server default.

`EnableRetryOnFailure()` replays the whole EF operation.
It derives from EF Core's `ExecutionStrategy`. Standard EF rules apply to user-started transactions: run them inside `ctx.Database.CreateExecutionStrategy().ExecuteAsync(...)`.

```csharp
var strategy = ctx.Database.CreateExecutionStrategy();
await strategy.ExecuteAsync(async () =>
{
    await using var tx = await ctx.Database.BeginTransactionAsync();
    // all reads and writes of the unit
    await ctx.SaveChangesAsync();
    await tx.CommitAsync();
});
```

`CADB0509` is not replayed. The commit is re-issued on the same transaction.

## Map entities

```csharp
modelBuilder.Entity<Robot>(b =>
{
    b.ToTable("robots", t => t.HasCheckConstraint("ck_robots_price", "price >= 0"));
    b.HasKey(e => e.Id);
    b.Property(e => e.Id).HasColumnType("id").ValueGeneratedOnAdd();
    b.Property(e => e.Name).HasColumnType("string");
    b.Property(e => e.Year).HasColumnType("int64");
    b.Property(e => e.Price).HasColumnType("numeric");
});
```

- A `string` key with `HasColumnType("id")` or `"oid"` gets a 24-character ObjectId generated on the client.
- That ObjectId is time-ordered. Under key-range sharding with high insert rates, use a `Guid` key instead.
- A `Guid` key maps to native `UUID`. Assign `Guid.NewGuid()` (random v4) in code for an even spread.
- A check-constraint failure surfaces as `DbUpdateException` with an inner `CamusException` code `CADB0303`.
- See [type-mapping.md](type-mapping.md) for every CLR type.

## Create and drop

- `await ctx.Database.EnsureCreatedAsync();` checks the database and sends `CREATE DATABASE IF NOT EXISTS` only when it is missing. Then it creates tables. It is idempotent.
- `await ctx.Database.EnsureDeletedAsync();` drops the connection-string database.

## LINQ translation

Supported:

- `Where`, `OrderBy`, `ThenBy`, `Skip`, `Take`, `Distinct`.
- Aggregates `Count`, `Sum`, `Average`, `Min`, `Max`, `Any`, and `GroupBy` with aggregates.
- Inner, left, right, and cross joins. Correlated subqueries such as `o.Items.Any(...)`.
- `ids.Contains(e.Id)` expands to `IN (@p0, @p1, ...)`.
- String methods: `StartsWith`, `EndsWith`, `Contains`, `ToUpper`, `ToLower`, `Trim`, `TrimStart`, `TrimEnd`, `Replace`, `Length`.
- `string.Compare(...)` and `CompareTo` against zero, for keyset pagination.
- Regex: `Regex.IsMatch`, `Regex.Replace`, `EF.Functions.RegexpLike`, `RegexpReplace`, `RegexpCount`, `RegexpSubstr`, `RegexpInstr`.
- Vectors: `EF.Functions.L2Distance`, `CosineDistance`, `InnerProduct`, `VectorDims`, `OctetLength`. Pair `OrderBy(distance)` with `Take(k)`.

Not supported:

- Lateral joins, window functions, `UNION`, `UNION ALL`.
- Per-row `Take`, `Skip`, or `First` inside a collection, such as filtered includes.
- A LINQ predicate over an `ARRAY(T)` property.

## Bulk update and delete

- `ExecuteUpdateAsync()` and `ExecuteDeleteAsync()` run one server-side statement.
- An unfiltered bulk statement is sent as `WHERE TRUE`. CamusDB requires a `WHERE` on `UPDATE` and `DELETE`.
- A filter with navigation, joins, subqueries, or paging becomes `WHERE <pk> IN (SELECT ...)`. It needs a single-column primary key.
- `SetProperty` values cannot read another table.
- Bulk operations bypass the change tracker and do not advance row-version tokens.

## Concurrency tokens

- `[Timestamp]` or `IsRowVersion()` on `byte[]`: the provider writes a fresh token on each insert and update.
- `[ConcurrencyCheck]` on `short`, `int`, or `long`: increment the value in code before `SaveChangesAsync()`.
- A stale write raises `DbUpdateConcurrencyException`.
- A conflict can surface at `CommitTransactionAsync()` after `SaveChangesAsync()` succeeded.

## Query result cache

```csharp
List<Order> recent = await ctx.Orders
    .Where(o => o.Status == 1)
    .OrderByDescending(o => o.Total)
    .Take(20)
    .WithCache("recent_orders", ttl: TimeSpan.FromSeconds(30), strict: true)
    .ToListAsync();
```

The cache serves single-table autocommit reads only. Joins, views, and explicit transactions read live storage.

## Migrations

`dotnet ef migrations add <Name>` and `dotnet ef database update` work. Supported operations:

| Operation | SQL shape |
| --- | --- |
| Create / drop / rename table | `CREATE TABLE IF NOT EXISTS`, `DROP TABLE`, `ALTER TABLE ... RENAME TO` |
| Add / drop / rename column | `ALTER TABLE ... ADD COLUMN`, `DROP COLUMN`, `RENAME COLUMN ... TO` |
| Alter nullability | `ALTER TABLE ... ALTER COLUMN ... SET NOT NULL` or `DROP NOT NULL` |
| Add / drop check constraint | `ADD CONSTRAINT ... CHECK (...)`, `DROP CONSTRAINT` |
| Add / drop foreign key | `ADD CONSTRAINT ... FOREIGN KEY ... REFERENCES`, `DROP CONSTRAINT` |
| Unique constraint / alternate key | `CREATE UNIQUE INDEX`, `ALTER TABLE ... DROP INDEX` |
| Create / drop / rename index | `CREATE [UNIQUE] INDEX IF NOT EXISTS`, `DROP INDEX`, `RENAME INDEX ... TO` |
| Comments | `COMMENT ON TABLE / COLUMN / INDEX ... IS ...` |
| Views | `migrationBuilder.CreateView(...)`, `DropView(...)`, `RenameView(...)` |
| Truncate | `migrationBuilder.TruncateTable(...)` |
| Seed data / raw SQL | `INSERT INTO ...`, passed through |

Limits:

- No in-place column type change. Plan a manual migration (new column, copy, drop).
- No add or drop of a primary key.
- No computed columns.
- Foreign keys run `NO ACTION` or `RESTRICT` on the server. EF applies cascade and set-null only to tracked dependents.
- Foreign-key cycles across two or more tables are not supported. Self-references work.
- A foreign key needs an index that leads with the referencing columns.

Views: map with `modelBuilder.Entity<T>().HasNoKey().ToView("name")`. Keep them out of `SaveChanges()`.
Refresh a materialized view with `ctx.Database.ExecuteSqlRawAsync("REFRESH MATERIALIZED VIEW name")`.

## Other helpers

- `await ctx.Database.TruncateTableAsync("table");` then `ctx.ChangeTracker.Clear();`. It cannot run inside an explicit transaction.
- Index comments: `b.HasIndex(u => u.Email, "email_idx").HasComment("...")`.
- Table and column comments use EF Core `HasComment`.

## Prepared statements

The driver auto-prepares repeated EF queries and `SaveChangesAsync()` statements. No EF configuration is needed.
Tune with `MaxAutoPrepare` and `AutoPrepareMinUsages` in the connection string.
