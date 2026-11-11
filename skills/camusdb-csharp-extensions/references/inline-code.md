# Inline C# code reference

Read this file before you write the body of an inline function
(`CREATE FUNCTION ... LANGUAGE csharp`) or an inline trigger
(`CREATE TRIGGER ... LANGUAGE csharp`).

## Syntax

```sql
CREATE [OR REPLACE] FUNCTION name(param TYPE [, ...]) RETURNS TYPE
  [CALLED ON NULL INPUT | RETURNS NULL ON NULL INPUT]
  LANGUAGE csharp AS $$ ...C# statements... $$;

ALTER FUNCTION name OWNER TO user_name;
DROP FUNCTION [IF EXISTS] name;
SHOW FUNCTIONS;
SHOW CREATE FUNCTION name;

CREATE TRIGGER name ON table {BEFORE | AFTER} event [, event ...]
  LANGUAGE csharp AS $$ ...C# statements... $$;
-- event: INSERT, UPDATE, DELETE

SHOW TRIGGERS [ON table];
SHOW CREATE TRIGGER name ON table;
DROP TRIGGER name ON table;
```

- Dollar quotes: `$$ ... $$` or `$tag$ ... $tag$`. Use a tag when the body
  contains `$$`. Outside these bodies, a bare `$` is a syntax error
  (`CADB0406`).
- `FUNCTION`, `RETURNS`, `CALLED`, `INPUT`, `LANGUAGE`, and `csharp` stay
  usable as identifiers.
- `SHOW CREATE TRIGGER` returns `EXTERNAL NAME` for a DLL trigger, or the
  dollar-quoted C# for an inline trigger.
- `SHOW TRIGGERS` lists both forms, with timing, events, form, owner, and
  source hash where it applies.

## Types

| SQL type | C# type |
| --- | --- |
| `INT64` | `long` |
| `FLOAT64` | `double` |
| `FLOAT32` | `float` |
| `BOOL` | `bool` |
| `STRING` | `string` |
| `UUID` | `Guid` |
| `BYTES` | `byte[]` |
| `DATETIME` | `DateTime` |
| `NUMERIC` | `decimal` |
| `ARRAY(E)` | `IReadOnlyList<T?>`, where `E` is one of the types above except `BYTES`. Elements are nullable. |

- Not supported in an inline signature: `OID`, `DATE`, `JSONB`, nested arrays.
  A refused signature gives `CADB0839`.
- A `NUMERIC` value must fit a .NET `decimal`. A value that does not fit fails
  with `CADB0810`.

## NULL rules

| Clause | Behavior |
| --- | --- |
| `RETURNS NULL ON NULL INPUT` (default) | Any NULL argument gives NULL. The body does not run. |
| `CALLED ON NULL INPUT` | The body runs. Arguments are nullable C# values. |

The body can always return `null`. That gives SQL NULL.

## Allowed code

Allowed in functions and triggers:

- Local variables, loops, local functions, lambdas, pattern matching,
  exceptions, strings, numeric operations, supported collections, and LINQ.

Refused in functions and triggers:

- Files, networking, processes, reflection, unsafe code, threads and tasks.
- Some allocation, formatting, and deeply nested type shapes.
- A deferred LINQ chain kept in a local. Call `ToList` or `ToArray` first.
- A regex call with no match timeout.

Refused in functions only:

- Async bodies and SQL calls.
- Calls to other SQL functions.
- The clock, `Random`, and `Guid.NewGuid`.

Refused in triggers only:

- An `await` on anything other than a `Sql` operation.
- `Task.Run`, `Task.Delay`, and an `await` inside a lambda or local function.

Triggers may use `DateTime.UtcNow` and `Guid.NewGuid`.

Every type and member must be on the allowlist. `CADB0839` lists each refused
construct with its body position. `CADB0838` gives compiler errors with line
and column.

## Names in a trigger body

| Name | Use |
| --- | --- |
| `NEW`, `OLD` | Generated nullable row properties, typed from the table. `NEW` is null for `DELETE`. `OLD` is null for `INSERT`. |
| `Operation`, `Timing`, `TableName`, `TriggerName` | The event and the trigger metadata. |
| `Sql` | Bind typed parameters with `Set`, then `await ExecuteAsync` or `QueryAsync`. Parameters clear after that SQL operation. |
| `Context` | The `ITriggerContext` row API. Use it for columns with no generated property. |
| An inline-function name of this database | Call it as a C# method. Its NULL rule and budget apply. |

Column-name rules:

- A C# keyword column uses a verbatim property: `NEW.@class`.
- Use the row API for a column that collides with a context name, starts with
  `__camus`, or has no supported property type:

```csharp
var t = Context.New!.GetString("Timing");
Context.New!.Set("Timing", "x");
```

Row rules:

- Change `NEW` only in `BEFORE INSERT` or `BEFORE UPDATE`.
- `OLD` is read-only. Rows in an `AFTER` trigger are read-only.

## Budgets

Each function call and each trigger fire runs under these budgets:

| Limit | Default | Setting |
| --- | --- | --- |
| Time per call or fire | 1 second | `inline_function_call_timeout_ms` |
| Managed allocation per call or fire | 16 MiB | `inline_function_allocation_limit_bytes` |
| Elements in `new T[n]` | 1,048,576 | `inline_function_array_limit` |
| Source size | 64 KiB of UTF-8 | `inline_function_source_limit_bytes` |
| Compile deadline, including the wait for a slot | 10 seconds | `inline_function_compile_timeout_ms` |
| Concurrent compiles on one node | 2 | `inline_function_compile_concurrency` |
| Inline functions per database | 256 | `max_inline_functions_per_database` |

- A runtime budget overrun fails the statement with `CADB0840`.
- Recursion is checked against the free stack space.
- The checks run between framework calls. One framework call can finish before
  CamusDB finds the overrun.
- In a trigger, time and memory spent in nested SQL do not count against the
  C# budget. SQL resource limits and the transaction mutation limit still apply.
- `CANCEL QUERY` does not stop a running inline function.

## Settings

All keys need a restart.

| Key | Default | Scope | Notes |
| --- | --- | --- | --- |
| `inline_functions_enabled` | `false` | cluster | Accept `CREATE FUNCTION`. Existing functions still run when off. |
| `inline_triggers_enabled` | `false` | cluster | Accept `CREATE TRIGGER ... LANGUAGE csharp`. Existing triggers still fire when off. |
| `inline_function_call_timeout_ms` | `1000` | cluster | Excludes nested trigger SQL. Zero or less turns it off. |
| `inline_function_allocation_limit_bytes` | `16777216` | cluster | Excludes nested SQL. Zero or less turns it off. |
| `inline_function_array_limit` | `1048576` | cluster | Zero or less turns it off. Other allocations still use the memory budget. |
| `inline_function_source_limit_bytes` | `65536` | cluster | Must be positive. |
| `inline_function_compile_timeout_ms` | `10000` | node | Must be positive. |
| `inline_function_compile_concurrency` | `2` | node | Must be positive. |
| `max_inline_functions_per_database` | `256` | cluster | Zero or less turns it off. |

Use the same cluster-scope values on every node.

## Consistency across nodes

- Use ordinal string comparisons when every node must give the same result.
- Keep server builds and globalization modes the same on all nodes.
  Culture-sensitive comparisons and the last bits of platform math can differ.
- Upgrade every node before you use an API that a new server build allows. A
  node that cannot compile the code fails with `CADB0845`.
