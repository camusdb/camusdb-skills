# DLL extensions reference

Read this file before you build, deploy, install, or upgrade a C# extension DLL.
DLL extensions are the only way to add table functions, aggregates, and
procedures, or code outside the inline allowlist.

## Project

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netstandard2.0</TargetFramework>
    <LangVersion>latest</LangVersion>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="CamusDB.Extensions.Abstractions" Version="0.17.*" />
  </ItemGroup>
</Project>
```

- Target `netstandard2.0` or `net10.0`.
- Pin the minor version of the servers, such as `0.17.*`. In `0.x`, a minor
  release can change the contract.
- A server loads an extension built against the same or an older contract. A
  newer contract fails with `CADB0803`.
- Do not deploy the contract DLL. The server uses its own copy and ignores a
  bundled one.

## Manifest

```csharp
using CamusDB.Extensions;

[assembly: CamusExtension("text_utils", "1.0.0")]
```

- The name is not case-sensitive. It starts with a letter or underscore, then
  letters, digits, or underscores.
- The version is SemVer `major.minor.patch`, with an optional `-prerelease`.
  No `+build` metadata.
- A missing or bad manifest stops startup with `CADB0827`.
- Declare dependencies with `Requires = new[] { "other_extension" }`.

## Scalar functions

```csharp
public static class TextFunctions
{
    [CamusFunction("slugify")]
    public static string Slugify(string value)
        => System.Text.RegularExpressions.Regex.Replace(
            value.Trim().ToLowerInvariant(), "[^a-z0-9]+", "-").Trim('-');

    [CamusFunction("greet", Aliases = new[] { "hello" })]
    public static string Greet(string? name, string greeting = "hello")
        => greeting + " " + (name ?? "stranger");

    [CamusFunction("random_token", IsVolatile = true)]
    public static string RandomToken() => System.Guid.NewGuid().ToString("N");
}
```

```sql
SELECT slugify(title) FROM posts WHERE slugify(title) = 'hello-world';
SELECT greet(NULL), hello('ann', 'hi');
SELECT text_utils.greet('ann');   -- qualified with the extension name
```

- Mark a public static method with `[CamusFunction]`.
- Names and aliases are not case-sensitive. A collision with a built-in, or a
  duplicate inside one extension, stops startup (`CADB0805`).
- Two extensions with the same member name cannot both be installed in one
  database (`CADB0832`). Unqualified names resolve against the current
  database's installed extensions.
- A nullable parameter (`long?`, `string?`) receives NULL as `null`. A NULL
  for a non-nullable parameter gives NULL without a call.
- Optional C# parameters set the accepted argument count. A missing argument
  gets the default value.
- Functions are deterministic by default. Set `IsVolatile = true` when the same
  arguments can give a different result.
  - A volatile function skips the result cache and cannot appear in `CHECK`.
  - A deterministic `DEFAULT` is evaluated once, at table creation, and stored
    as a constant. A volatile default runs for each inserted row.
- A throw fails the statement with `CADB0809`.
- Calls can run at the same time. Make the code thread-safe.
- Calls run on the coordinating node. CamusDB does not push them to peers in a
  distributed scan.

## Table functions

```csharp
public static class TextTables
{
    [CamusTableFunction("split_words", Column = "word")]
    public static IEnumerable<string> SplitWords(string text)
    {
        foreach (string word in text.Split(
            new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries))
            yield return word;
    }
}
```

```sql
SELECT word FROM split_words('inventory low stock');
SELECT w.word, d.weight
FROM split_words(@text) AS w JOIN dictionary d ON d.term = w.word;
```

- Return `IEnumerable<T>` of one supported scalar or array type. One output
  column only. No `LATERAL` references.
- `Column` names the output. Otherwise the function name or the alias names it.
- A NULL for a non-nullable parameter, or a null sequence, gives zero rows. A
  null element gives a NULL row.
- The server reads the sequence lazily and disposes the enumerator on `LIMIT`,
  cancel, or error. It checks cancellation between yielded values only.
- There is no output row cap.
- A join can read the function again. A non-volatile function must give the
  same rows again. A volatile function's rows are kept for the statement.
- `CountRowsMethod` names a cheap public static method with the same parameters
  that returns `long?`. The planner calls it for constant arguments. `EXPLAIN`
  shows the estimate on `function-scan`.
- Table functions run on the coordinator. Failures give `CADB0809`.

## Aggregates

```csharp
[CamusAggregate("geomean")]
public sealed class GeoMean : ICamusAggregate<GeoMean.State>
{
    public sealed class State { public double LogSum; public long Count; }

    public State Create() => new State();

    public void Accumulate(State state, double price)
    {
        if (price > 0) { state.LogSum += Math.Log(price); state.Count++; }
    }

    public double? Finish(State state)
        => state.Count == 0 ? null : Math.Exp(state.LogSum / state.Count);
}
```

```sql
SELECT category, geomean(price) AS typical_price
FROM items GROUP BY category HAVING geomean(price) > 10;
```

- `Create` makes one state per group. `Accumulate` returns `void` and takes
  the state, then the SQL arguments. Write exactly one public `Accumulate`.
- `Finish` returns a supported type, arrays included.
- A global aggregate over zero rows still calls `Create` and `Finish`.
- Non-nullable arguments skip NULL rows.
- One instance serves concurrent statements. Keep group data in the state.
- Set `StateSizeHint` when a state needs more than the 64-byte default.
- Use aggregates in the select list and `HAVING`. They are refused in `WHERE`,
  `JOIN ON`, `GROUP BY`, defaults, and checks.
- No window use, no `DISTINCT` arguments, no distributed partial aggregation.
  `Merge` is not called in this version.

## DLL triggers

```csharp
public sealed class AuditTrigger : ICamusTrigger
{
    public Task FireAsync(ITriggerContext context)
    {
        IRow? row = (IRow?)context.New ?? context.Old;
        return context.Sql.ExecuteAsync(
            "INSERT INTO audit_log (op, tbl, name) VALUES (@op, @tbl, @name)",
            new Dictionary<string, object?>
            {
                ["op"] = context.Operation.ToString(),
                ["tbl"] = context.TableName,
                ["name"] = row?.GetString("name"),
            });
    }
}
```

```sql
CREATE TRIGGER audit ON orders
AFTER INSERT, UPDATE, DELETE
EXTERNAL NAME 'MyCompany.Ext.AuditTrigger';
```

- A public class with a public parameterless constructor that implements
  `ICamusTrigger`. `EXTERNAL NAME` is the full CLR type name.
- Install the extension first. A loaded but uninstalled extension gives
  `CADB0830`.
- One instance serves concurrent statements. Do not keep per-statement state
  in instance or static fields.
- `context.Old` and `context.New` hold every column, large values included.
- An extension update switches triggers to the new build. You do not need to
  create them again.

### Typed trigger rows

```csharp
public sealed class OrderRow
{
    [CamusColumn("customer_name")]
    public string? CustomerName { get; set; }
}

public sealed class TrimCustomerName : ICamusTrigger<OrderRow>
{
    public Task FireAsync(ITriggerContext<OrderRow> context)
    {
        if (context.New is { } row)
            row.CustomerName = row.CustomerName?.Trim();
        return Task.CompletedTask;
    }
}
```

- Public get/set properties map by name (not case-sensitive) or by
  `[CamusColumn]`. Fields, indexers, and init-only properties do not map.
- Property types must match the column's CLR type exactly. A mismatch fails
  `CREATE TRIGGER` with `CADB0836` and a list of problems.
- Use nullable properties for NULL or for values not yet known, such as a row
  id in `BEFORE INSERT`. An unexpected NULL in a non-nullable property fails
  with `CADB0818`.
- `BEFORE` triggers write back only the changed `NEW` properties. Changes to
  `AFTER` objects are ignored. Cast to `ITriggerContext` for untyped rows.
- A mapped column cannot be dropped or renamed while the trigger exists
  (`CADB0530`). Drop the trigger first.
- Typed rows allocate an object per row image. Use untyped triggers when that
  cost matters.

## Procedures

```csharp
[CamusProcedure("transfer", MinArguments = 3, MaxArguments = 3)]
public sealed class Transfer : ICamusProcedure
{
    public async Task ExecuteAsync(IProcedureContext context)
    {
        string from = (string)context.Arguments[0]!;
        string to = (string)context.Arguments[1]!;
        long amount = (long)context.Arguments[2]!;

        await context.Sql.ExecuteAsync(
            "UPDATE accounts SET balance = balance - @amount WHERE name = @name",
            new Dictionary<string, object?> { ["amount"] = amount, ["name"] = from });
        await context.Sql.ExecuteAsync(
            "UPDATE accounts SET balance = balance + @amount WHERE name = @name",
            new Dictionary<string, object?> { ["amount"] = amount, ["name"] = to });
    }
}
```

```sql
CALL transfer('alice', 'bob', 30);
CALL transfer(@from, @to, 10 * 3);
```

- Arguments are evaluated first. A count outside `MinArguments` to
  `MaxArguments` is refused.
- The procedure runs in the `CALL` transaction. A throw rolls back all of it
  with `CADB0822`.
- `CALL` returns the total changed rows and no result set. Send it with an
  execute or non-query API. It cannot be prepared.
- `CALL` needs a superuser.

## SQL from a DLL trigger or procedure

- `context.Sql` runs in the same transaction, with the invoking user's
  privileges.
- `ExecuteAsync` runs `INSERT`, `UPDATE`, or `DELETE` and returns the changed
  row count. `QueryAsync` runs `SELECT` and returns row copies.
- DDL and `BEGIN`, `COMMIT`, `ROLLBACK` fail with `CADB0820`.
- Parameter keys work with or without the leading `@`.
- Nested writes count against `max_mutations_per_transaction` and fire the
  target table's triggers.

## Supported values

| CamusDB type | C# type |
| --- | --- |
| `INT64` | `long` |
| `FLOAT64` | `double` |
| `FLOAT32` | `float` |
| `BOOL` | `bool` |
| `STRING` | `string` |
| `OID` | `string` with the ObjectId text |
| `UUID` | `Guid` |
| `BYTES`, vectors included | `byte[]` |
| `DATE`, `DATETIME` | UTC `DateTime`. A returned `DateTime` is `DATETIME`. |
| `NUMERIC` | `decimal` |
| `ARRAY(E)` | `IReadOnlyList<T>` of the element's type |

- An `INT64` argument converts to `double` or `decimal`. A UUID-formatted
  `STRING` converts to `Guid`. JSON arrives as `STRING`.
- Use `IReadOnlyList<T>` in signatures. `long[]` is refused. A method that
  returns `IReadOnlyList<long>` can return an array.
- NULL elements need nullable value elements, such as `IReadOnlyList<long?>`.
  Nested arrays and arrays of `byte[]` are not supported.
- A `NUMERIC` that does not fit `decimal` fails with `CADB0810`. Returned
  decimals with more than nine fractional digits round half away from zero.
- Byte arrays are copies. Changing one does not change stored data.
- Typed row access: `IRow.GetArray<T>` and `IWritableRow.SetArray<T>`.

## Deploy

```yaml
extension_assemblies:
  - /opt/camusdb/extensions/MyCompany.Ext.dll

unsafe_extension_assemblies:
  - /opt/camusdb/extensions/MyCompany.Reviewed.dll
```

- Paths are absolute. Flags: `--extension-assemblies` and
  `--unsafe-extension-assemblies`, with space-separated paths.
- Both settings need a restart. To load a new build, replace the file and
  restart.
- The DLL is not stored in the database or replicated. Copy the same files to
  every node.
- A bad path, a missing file, a missing manifest, or a duplicate name and
  version stops startup.
- The SHA-256 covers only the extension DLL. Deploy the whole folder of an
  unsafe extension as one unit.

### Safe and unsafe lists

- `extension_assemblies` (safe): CamusDB inspects the metadata. It refuses
  file, process, network, native, and dynamic-load APIs (`CADB0806`,
  `CADB0807`).
- `unsafe_extension_assemblies`: no checks. The code can load private
  dependencies from its own folder.
- Safe mode is not a sandbox. The code can use unbounded CPU or memory. A stack
  overflow can stop the server. The assembly lists are the security boundary.

### Install, update, and drop

```sql
CREATE EXTENSION text_utils;                  -- highest loaded version
CREATE EXTENSION text_utils VERSION '1.0.0';
ALTER EXTENSION text_utils UPDATE;            -- highest loaded version
ALTER EXTENSION text_utils UPDATE TO '2.0.0';
DROP EXTENSION text_utils;
```

- Superuser only. In a cluster, run these on the database's schema leader.
- The database records the name, version, and SHA-256. Different databases can
  use different versions on one node.
- Install each `Requires` extension first (`CADB0831`). You cannot drop an
  extension that another installed extension requires.
- A drop is refused while defaults, `CHECK` constraints, views, materialized
  views, or triggers depend on it. An update is refused if the target lacks a
  member in use. The error lists the dependents (`CADB0833`). There is no
  `CASCADE`.
- Before an install or update, the coordinator checks the other nodes. A
  responding node without the build gives `CADB0835`. A node that does not
  respond is skipped.

### Rolling upgrade

1. Copy the new DLL beside the old one on every node. List both paths. Restart
   the nodes one at a time.
2. Run `ALTER EXTENSION text_utils UPDATE TO '2.0.0'` in each database.
3. When no database uses the old version, remove its path and file. Restart the
   nodes again.

### Missing builds

- A node without the installed build, or with a different hash, still starts.
- Statements that use that extension fail with `CADB0834`. So do writes to
  tables with its triggers.
- `SHOW EXTENSIONS` shows the build as `missing`. Deploy the exact build and
  restart.

## SHOW EXTENSIONS

Superuser only. Node-local. Compare `sha256` across nodes to find a bad deploy.

| Status | Meaning in the current database |
| --- | --- |
| `available` | The node loaded the build. This database does not use it. |
| `installed` | This database uses the build, and the node loaded it. |
| `missing` | This database selected a build that the node cannot run. `path` and `trust` are null. |

Columns: `name`, `version`, `status`, `path`, `sha256`, `trust`, `requires`,
`functions`, `triggers`, `procedures`, `aggregates`, `table_functions`, and
`signatures` (for example `split_words(STRING) -> SETOF STRING`).

## Dumps and the playground

- A logical dump includes `CREATE EXTENSION name VERSION 'version'`, but no DLLs
  and no trigger definitions. Deploy the files before the reimport. Create the
  triggers again after you load the rows.
- The browser playground cannot load extensions.

## Limits

- Triggers and procedures nest up to 16 levels (`CADB0819`).
- Rows kept for `AFTER` triggers count against `spill_threshold_bytes`
  (`CADB0826`).
- Cancellation is cooperative. A write cannot be cancelled after it starts.
- CamusDB cannot stop a DLL method that never returns.
