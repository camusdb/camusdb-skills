---
name: camusdb-csharp-extensions
description: Writes, reviews, and deploys C# code that runs inside CamusDB. Covers inline functions (CREATE FUNCTION ... LANGUAGE csharp AS $$ ... $$, CREATE OR REPLACE FUNCTION, DROP FUNCTION, CALLED ON NULL INPUT), inline triggers (CREATE TRIGGER ... LANGUAGE csharp, NEW, OLD, Sql.Set), DLL extensions built on CamusDB.Extensions.Abstractions ([CamusFunction], [CamusTableFunction], [CamusAggregate], ICamusTrigger, [CamusProcedure]), CREATE EXTENSION, ALTER EXTENSION, CREATE TRIGGER ... EXTERNAL NAME, CALL, SHOW FUNCTIONS, SHOW TRIGGERS, SHOW EXTENSIONS, and the settings inline_functions_enabled, inline_triggers_enabled, and extension_assemblies. Use it when adding custom SQL functions, triggers, aggregates, or procedures to CamusDB, choosing inline code or a DLL, or fixing CADB08xx errors such as CADB0809, CADB0818, CADB0823, CADB0834, CADB0838, CADB0839, CADB0840, CADB0841, CADB0844, CADB0845, or CADB0846.
---

# CamusDB C# extensions

CamusDB runs C# code inside the server process. The code is called from SQL.
There are two forms:

- **Inline code.** A `CREATE FUNCTION` or `CREATE TRIGGER` statement holds a C#
  body in dollar quotes. No DLL. The operator turns each feature on. Users with
  the right privileges then create the objects.
- **DLL extensions.** A reviewed class library that the operator copies to every
  node and lists in `config.yml`. A superuser installs it in each database with
  `CREATE EXTENSION`.

Other skills cover nearby topics:

- `camusdb-sql`: the SQL that calls a function or fires a trigger.
- `camusdb-transactions`: the failed-statement rule, rollback-only transactions,
  and retries.
- `camusdb-local-dev`: `config.yml`, restarts, and cluster setup.
- `camusdb-troubleshooting`: error codes outside `CADB08xx`.

## Workflow

1. Choose inline code or a DLL with the table in "Choose inline or DLL".
2. Inline: confirm that the operator set the feature flag on every node and
   restarted. See "Enable inline code".
3. Write the body. Read [references/inline-code.md](references/inline-code.md)
   for the allowed types, the allowed code, and the budgets.
   For a DLL, read [references/dll-extensions.md](references/dll-extensions.md).
4. In a cluster, run function, trigger, and extension DDL on the database's
   schema leader.
5. Check the result with `SHOW FUNCTIONS`, `SHOW TRIGGERS`, or `SHOW EXTENSIONS`.
6. On a `CADB08xx` error, read
   [references/error-codes.md](references/error-codes.md).

## Choose inline or DLL

| Need | Use |
| --- | --- |
| A scalar calculation in SQL, no deploy step | Inline function |
| Normalization, validation, or audit rows on one table | Inline trigger |
| A table function (rows from C#) | DLL |
| A custom aggregate | DLL |
| A procedure called with `CALL` | DLL |
| Code outside the inline allowlist (for example, files or networking) | DLL. Unsafe list only, after review. |
| A function that must read the clock or `Random` | DLL with `IsVolatile = true`. Inline functions refuse them. |
| No operator access to the nodes | Inline, if the operator enabled it |

Inline code is checked against an allowlist and runs under time, memory, and
array budgets. DLL code has no budget: CamusDB cannot stop a DLL method that
never returns.

## Enable inline code

Both features are off by default. Each has its own flag:

```yaml
inline_functions_enabled: true
inline_triggers_enabled: true
```

- Set the same value on every node. The scope is the cluster.
- Restart each node after a change.
- A disabled flag blocks only new definitions. Existing functions stay callable.
  Existing triggers still fire.
- With the flag off, `CREATE FUNCTION` or `CREATE TRIGGER ... LANGUAGE csharp`
  fails with `CADB0841`. A server build with no compiler gives the same code.
- The browser playground has no inline compiler. It cannot load DLLs either.

Configure a process heap hard limit as the memory backstop. The budgets are
checked between framework calls, so one framework call can finish before a
limit is found.

## Inline functions

```sql
CREATE FUNCTION slugify(s STRING) RETURNS STRING LANGUAGE csharp AS $$
    return s.Trim().ToLowerInvariant().Replace(' ', '-');
$$;

SELECT slugify('  New Book  ');  -- 'new-book'
SHOW FUNCTIONS;
SHOW CREATE FUNCTION slugify;
```

- The body is the statements of one C# 12 method. Use the parameter names and
  case of the declaration.
- System, collections, globalization, LINQ, text, and regex namespaces are
  imported.
- Dollar quotes keep the body text as written, with semicolons and quotes. If
  the body contains `$$`, use a tag: `$body$ ... $body$`.
- Names are not case-sensitive. They share one namespace with built-ins and
  extension members. There are no overloads and no default arguments.
- Allowed types: `INT64`, `FLOAT64`, `FLOAT32`, `BOOL`, `STRING`, `UUID`,
  `BYTES`, `DATETIME`, `NUMERIC`, and `ARRAY(E)` of those except `BYTES`.
  `OID`, `DATE`, `JSONB`, and nested arrays are refused.
- The default is `RETURNS NULL ON NULL INPUT`. A NULL argument returns NULL and
  the body does not run. Add `CALLED ON NULL INPUT` to receive nullable C#
  arguments. A body can always return null.

```sql
CREATE FUNCTION display_label(s STRING) RETURNS STRING
CALLED ON NULL INPUT LANGUAGE csharp AS $$
    return s ?? "untitled";
$$;

CREATE OR REPLACE FUNCTION slugify(s STRING) RETURNS STRING LANGUAGE csharp AS $$
    return s.Trim().ToLowerInvariant().Replace(' ', '_');
$$;

ALTER FUNCTION slugify OWNER TO app_owner;
DROP FUNCTION IF EXISTS slugify;
```

An inline function is deterministic. It cannot run SQL, call another SQL
function, read the clock, use `Random`, or call `Guid.NewGuid`. Use ordinal
string comparisons when all nodes must agree.

### Dependencies

- `DROP FUNCTION`, or a replace that changes the parameter or result types,
  fails with `CADB0844` while a view, a materialized view, or a `CHECK` calls
  the function.
- A replace that changes only the body keeps the owner and the dependents.
  Dependents use the new body in later statements. A materialized view keeps
  its stored rows until `REFRESH`.
- A deterministic column default is computed once, at table creation. A new
  body does not change it.
- A new body clears cached results that depend on the function.
- The dependency check does not read inline-trigger bodies. See "Trigger
  recompilation".

### Compilation across nodes

- The schema leader validates the body at `CREATE`. The schema replicates the
  source and its hash, not a DLL.
- Each node compiles at first use and caches the result.
- `SHOW FUNCTIONS` lists inline functions only, not DLL members. It shows the
  signature, NULL rule, owner, generation, source hash, rule revision, and the
  compile state on this node: `not compiled`, `compiled`, or a failure reason.
  An `unloading` row is old code that a running statement still holds.
- A node that cannot compile required code fails the statement with
  `CADB0845`. Upgrade every node before you use an API that a new server build
  allows.

## Inline triggers

```sql
CREATE TABLE posts (id INT64 PRIMARY KEY, title STRING, slug STRING);
CREATE TABLE post_audit (
  id OID PRIMARY KEY DEFAULT (gen_id()), op STRING, post_id INT64
);

CREATE TRIGGER normalize ON posts BEFORE INSERT, UPDATE LANGUAGE csharp AS $$
    if (NEW!.title is { } title)
        NEW.slug = slugify(title);
$$;

CREATE TRIGGER audit ON posts AFTER INSERT, UPDATE, DELETE LANGUAGE csharp AS $$
    await Sql.Set("op", Operation.ToString()).Set("id", (OLD ?? NEW)!.id)
        .ExecuteAsync("INSERT INTO post_audit (op, post_id) VALUES (@op, @id)");
$$;
```

- `NEW` and `OLD` are typed from the table. `NEW` is null for `DELETE`. `OLD`
  is null for `INSERT`.
- Change `NEW` only in a `BEFORE INSERT` or `BEFORE UPDATE` trigger. `OLD` and
  `AFTER` rows are read-only.
- Run SQL with `Sql`: bind with `Set`, then `await ExecuteAsync` or
  `QueryAsync`. The parameters clear after each SQL operation.
- Call a database inline function as a C# method. Its NULL rule and budget apply.
- A trigger may use `DateTime.UtcNow` and `Guid.NewGuid`. A function may not.
- A trigger can await only `Sql` operations. `Task.Run`, `Task.Delay`, and an
  `await` inside a lambda or local function are refused.
- A throw fails the statement with `CADB0818`. A caught nested SQL failure
  still leaves the transaction rollback-only (`CADB0823`).

For the body names, the row API, and column-name rules, read
[references/inline-code.md](references/inline-code.md).

### Trigger rules for both forms

- A trigger fires once per affected row, in creation order, inside the
  statement's transaction. Nested SQL shares that transaction.
- `BEFORE` runs before validation. The final row must still pass type,
  `NOT NULL`, length, foreign-key, and `CHECK` rules. `BEFORE DELETE` cannot
  change the row, but it can throw to stop the delete.
- `AFTER` runs after the statement writes all rows and passes foreign-key
  checks.
- Triggers fire for `INSERT`, `UPDATE`, `DELETE`, `RETURNING`,
  `INSERT ... SELECT`, DML from procedures and triggers, and the typed HTTP and
  gRPC write APIs.
- Triggers do not fire for `TRUNCATE`, TTL expiry, schema backfills and
  rewrites, materialized-view refresh, restore, or dropped-table recovery.
- On `ON CONFLICT` and `UPSERT`, `BEFORE INSERT` runs on each proposed row. A
  conflict update then fires the `UPDATE` triggers. A skipped row fires nothing
  more.
- A `BEFORE` trigger must not change, through `Sql`, a row that the outer
  statement already read and still writes. That fails with `CADB0825`.
- Create triggers on plain tables only. A recovered dropped table has no
  triggers. Recreate them.

### Trigger recompilation

- The first fire after a table change or an inline-function change recompiles
  the body on each node. A new column or a replaced function is picked up.
- `DROP COLUMN` and `DROP FUNCTION` do not check inline-trigger bodies. If a
  body still names the removed member, each write that fires it fails with
  `CADB0845`. Drop the trigger and create it again with a valid body.

## Privileges and ownership

| Action | Needs |
| --- | --- |
| `CREATE FUNCTION` | `CREATE` on the database, or superuser. The creator owns it. |
| `CREATE OR REPLACE FUNCTION` | `CREATE` and ownership, or superuser |
| `DROP FUNCTION`, `ALTER FUNCTION ... OWNER TO` | Ownership, or superuser |
| `SHOW FUNCTIONS`, `SHOW CREATE FUNCTION` | `SELECT` or `CREATE` on the database |
| `CREATE TRIGGER` (both forms) | `ALTER` on the table |
| `DROP TRIGGER` | `ALTER` or superuser. The owner of an inline trigger can also drop it, if the database is still visible to the owner. |
| `SHOW TRIGGERS`, `SHOW CREATE TRIGGER` | `SELECT` or `ALTER` on the database |
| `CREATE`, `ALTER`, `DROP EXTENSION` | Superuser |
| `CALL` | Superuser (no procedure grants yet) |
| `SHOW EXTENSIONS` | Superuser. Node-local. |

- Every caller also needs database visibility.
- A function call does not get the owner's privileges. The body cannot run SQL.
- If a function owner is dropped, only a superuser can change the function.
- An inline trigger's SQL runs with the **owner's** privileges, not the
  writer's. Grant the owner what that SQL needs. Changes to `NEW` stay part of
  the writer's own write.
- If the trigger owner is dropped, each fire fails with `CADB0846`. A new user
  with the same name does not get the ownership.
- A trigger created with authentication off has no owner. Its SQL uses the
  writer's privileges. A DLL trigger also uses the writer's privileges.

```sql
SHOW TRIGGERS ON posts;               -- omit ON to list all tables
SHOW CREATE TRIGGER normalize ON posts;
DROP TRIGGER normalize ON posts;
```

## DLL extensions in short

```sql
CREATE EXTENSION text_utils;                  -- highest loaded version
CREATE EXTENSION text_utils VERSION '1.0.0';  -- exact version
SELECT text_utils.greet('ann');
CREATE TRIGGER audit ON orders AFTER INSERT, UPDATE, DELETE
EXTERNAL NAME 'MyCompany.Ext.AuditTrigger';
CALL transfer('alice', 'bob', 30);
ALTER EXTENSION text_utils UPDATE TO '2.0.0';
DROP EXTENSION text_utils;
SHOW EXTENSIONS;
```

- Reference `CamusDB.Extensions.Abstractions` with the server's minor version,
  such as `0.17.*`. A newer contract fails with `CADB0803`.
- Declare `[assembly: CamusExtension("name", "1.0.0")]` once.
- Copy the DLL to every node, list its absolute path in `extension_assemblies`,
  and restart. The DLL is not replicated.
- The database records the name, version, and DLL SHA-256. A node without
  that exact build fails statements that use it with `CADB0834`.
- Safe mode is a review guardrail, not a sandbox. Deploy reviewed code only.

Read [references/dll-extensions.md](references/dll-extensions.md) for the
attributes, signatures, types, deploy steps, and rolling upgrade.

## Side effects and retries

- `Sql` writes commit and roll back with the outer statement.
- CamusDB can retry a statement after a serializable conflict or a schema
  change. A network call, a file write, or an in-memory counter can then run
  more than once. Make such effects idempotent, or do them after commit.
- After a nested SQL error, the transaction is rollback-only, even when the
  code catches the exception. There are no savepoints. A return then fails with
  `CADB0823`. A `COMMIT` rolls back with `CADB0824`.
- Triggers and procedures nest up to 16 levels (`CADB0819`). Their writes count
  against `max_mutations_per_transaction`.
- `CANCEL QUERY` does not stop a running inline function. Its call budget
  limits it.

## Common mistakes

- Creating an inline function before the operator enables
  `inline_functions_enabled` on every node and restarts. It fails with `CADB0841`.
- Expecting `inline_functions_enabled` to also allow inline triggers. Set
  `inline_triggers_enabled` too.
- Running function or trigger DDL on a node that is not the schema leader.
  Other nodes reject trigger DDL, and the error names the leader.
- Overloading a name, or giving a parameter a default value. Neither exists.
- An `OID`, `DATE`, or `JSONB` parameter in an inline signature (`CADB0839`).
- `DateTime.UtcNow`, `Random`, or `Guid.NewGuid` in an inline function. Use a
  trigger, or a volatile DLL function.
- A regex call with no match timeout, or a deferred LINQ chain kept in a
  local. Both are refused. Call `ToList` or `ToArray`.
- Culture-sensitive string comparison. Results can differ between nodes. Use
  ordinal comparison.
- Dropping a column or a function that an inline trigger body still names.
  Writes then fail with `CADB0845`.
- Dropping the user who owns an inline trigger. Fires then fail with `CADB0846`.
- Catching a nested SQL exception and continuing. The transaction stays
  rollback-only.
- An HTTP call or an email inside a trigger. A statement retry can repeat it.
- A long loop in a DLL method. Nothing stops it.

## References

Read these files in this skill:

- [references/inline-code.md](references/inline-code.md): read before you write
  an inline function or trigger body. Types, NULL rules, allowed code, trigger
  body names, budgets, and settings.
- [references/dll-extensions.md](references/dll-extensions.md): read before you
  build, deploy, install, or upgrade a DLL extension. Attributes, signatures,
  supported values, typed trigger rows, procedures, safe and unsafe lists, and
  `SHOW EXTENSIONS`.
- [references/error-codes.md](references/error-codes.md): read when you see any
  `CADB08xx` code.

Public docs, under `https://camusdb.github.io/docs/` (append the page name):

- `csharp-extensions` (`#inline-functions`, `#inline-triggers`,
  `#trigger-recompilation-and-ownership`, `#deploy-extensions`).
- `configuration` (`#inline-c-functions-and-triggers`), `settings-reference`
  (`#inline-c-functions-and-triggers`).
- `error-codes`, `sql-expression-syntax` (dollar quotes), `sql-transactions`
  (`#failed-statements`), `logical-dump-and-reimport`.
