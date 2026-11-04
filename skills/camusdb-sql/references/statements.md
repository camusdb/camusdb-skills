# CamusDB statement syntax summary

Read this file when you need the exact form of a statement. Square brackets
mark optional parts. `|` separates alternatives. Schema DDL (`CREATE TABLE`
columns, `ALTER TABLE`, `CREATE INDEX`, sequences) belongs to the
`camusdb-schema-design` skill. `BEGIN`, `COMMIT`, and isolation belong to the
`camusdb-transactions` skill. `EXPLAIN` and hints belong to the
`camusdb-query-performance` skill.

## Literals

| Type | Literal |
| --- | --- |
| `INT64` | `42`, `200_000`, `0xFF`; past the range fails with `CADB0420` |
| `NUMERIC` | `NUMERIC '0.1'` (typed); a bare decimal is a float in expressions |
| `FLOAT64` | `3.14`, `1e10`, `CAST('NaN' AS float64)`; `1e400` fails with `CADB0421` |
| `STRING` | `'hello'` or `"hello"`; `E'a\tb'` for escapes |
| `BOOL` | `true`, `false` |
| `OID` | `'507f1f77bcf86cd799439011'` (24 hex chars), or `str_id('...')` |
| `UUID` | `'550e8400-e29b-41d4-a716-446655440000'`, or 32 hex digits |
| `DATE` | `'2026-03-15'` |
| `DATETIME` | `'2026-03-15T12:00:00Z'` (UTC, ISO-8601) |
| `BYTES` | `X'DEADBEEF'` or `x'...'`, an even digit count |
| `ARRAY(T)` | `ARRAY[1, 2, 3]`, `ARRAY[]` |
| `JSONB` | `JSONB '{"a": 1}'`, `'{"a": 1}'::jsonb` |

CamusDB parses date and datetime text as UTC. The date functions reject a
timestamp string with no `Z` or offset, such as `'2024-06-15 10:30:00'`.

## Queries

```sql
SELECT [DISTINCT] expr [AS alias] [, ...]
FROM source
  [AS OF SYSTEM TIME '-10s' | '<timestamp>' | <epoch-ms> | @p]
[WHERE condition]
[GROUP BY expr [, ...]]
[HAVING condition]
[ORDER BY expr [ASC | DESC] [, ...]]
[LIMIT n | @p] [OFFSET m | @p]
[FOR UPDATE [OF t] [NOWAIT | SKIP LOCKED]];

-- SELECT without FROM: projections, LIMIT, and OFFSET only
SELECT expr [AS alias] [, ...] [LIMIT n] [OFFSET m];
```

`source` is one of:

```sql
table [[AS] alias]
table@{FORCE_INDEX=index_name}
(SELECT ...) [AS] alias                         -- derived table, alias required
a [INNER] JOIN b ON cond
a LEFT [OUTER] JOIN b ON cond
a RIGHT [OUTER] JOIN b ON cond                  -- left operand must not be a join
a CROSS JOIN b
a, b [, ...]                                    -- comma join; equalities in WHERE become join keys
fn(args) [[AS] alias[(col)]]                    -- set-returning function: generate_series, jsonb_each
```

`FULL [OUTER] JOIN` and a `RIGHT JOIN` after another join fail with `CADB0533`.
`SELECT *` over a `RIGHT JOIN` lists the preserved table's columns first.

Predicates:

```sql
x [NOT] BETWEEN lo AND hi
x [NOT] IN (v1, @p, column_name)                 -- an expression item is a syntax error
x [NOT] IN (SELECT one_column FROM ...)          -- not correlated
[NOT] EXISTS (SELECT ... )                       -- correlated only in the WHERE of a SELECT
x op ANY | SOME | ALL (array_or_subquery)        -- op: = <> != < <= > >=
x [NOT] LIKE p | x [NOT] ILIKE p | x ~ p | x ~* p | x !~ p | x !~* p
x IS NULL | x IS NOT NULL
CASE WHEN c THEN r [...] [ELSE r] END
CASE x WHEN v THEN r [...] [ELSE r] END
IF(c, a, b)
```

- A column in an `IN` list reads the same row as `x`. Only a list of literals or
  parameters can become index seeks.
- `HAVING` reads a group key, a select item, or an aggregate. A qualified key works,
  and so does `COUNT(*) IN (1, 2)`.
- An uncorrelated subquery runs once, in any expression position and at any depth.
  `ORDER BY` and `GROUP BY` refuse a subquery with `CADB0400`.
- A correlated `EXISTS` outside the `WHERE` of a `SELECT` fails with `CADB0533`.
- A correlated `IN`, `NOT IN`, or quantified comparison is refused. A correlated
  scalar subquery fails as an unknown alias.

`AS OF SYSTEM TIME` reads a past snapshot. A plain read with it runs in
autocommit only. It applies to every join and subquery of the statement. See
https://camusdb.github.io/docs/time-travel-reads.

`UNION`, `INTERSECT`, `EXCEPT`, window functions (`OVER`), and `generate_series` are
in [set-and-window.md](set-and-window.md).

`FOR UPDATE` locks each returned row until the transaction ends. `FOR NO KEY UPDATE`
is a synonym. `FOR SHARE` and `FOR KEY SHARE` fail with `CADB0533`. Isolation rules,
errors, and restrictions are in the `camusdb-transactions` skill.

## Writes

```sql
INSERT INTO t [(col, ...)] VALUES (expr | DEFAULT, ...) [, (...)] [RETURNING list];
INSERT INTO t [(col, ...)] SELECT ... [RETURNING list];
INSERT ... ON CONFLICT [(col, ...) | ON CONSTRAINT name] DO NOTHING [RETURNING list];
INSERT ... ON CONFLICT ((col, ...) | ON CONSTRAINT name)
  DO UPDATE SET col = expr [, ...] [WHERE cond] [RETURNING list];
UPSERT INTO t [(col, ...)] { VALUES ... | SELECT ... } [RETURNING list];
UPDATE t SET col = expr [, ...] WHERE condition [LIMIT n] [RETURNING list];
DELETE FROM t WHERE condition [LIMIT n] [RETURNING list];
TRUNCATE [TABLE] t [RESTART IDENTITY | CONTINUE IDENTITY];
```

`SHOW ORPHAN TABLES` and `CREATE TABLE t2 RELINK TO '<id>'` recover truncated rows
within the retention window.

- Without a column list, `INSERT` targets every column in schema order.
- `WHERE` is required in `UPDATE` and `DELETE`.
- `RETURNING list` is a select list over the target table: `*`, columns,
  scalar expressions, `AS` aliases, parameters.
- `RETURNING` returns the stored row image: the inserted row, the new row after
  `UPDATE`, or the deleted row. `OLD` and `NEW` qualifiers are not supported.
- A repeated target column fails with `CADB0403` before any write. Names compare
  without case. `SET v = 1, v = 2` and `(id, v, V)` both fail.
- Any failed statement ends an explicit transaction. There are no savepoints.
- `CONTINUE IDENTITY` is the default. `RESTART IDENTITY` restarts only
  sequences owned by identity columns of the table.

### RETURNING on each endpoint

| Endpoint | Result |
| --- | --- |
| HTTP `/execute-sql-query`, `/execute-sql-query-stream` | `columns`, then `rows`. Refuses a write with no `RETURNING`. |
| HTTP `/execute-sql-non-query` | `rows` (the count), plus `columns` and `returningRows`. |
| gRPC `ExecuteQuery` | `ResultSchema`, then `ResultRow` messages. Sent after the commit. |
| gRPC `ExecuteNonQuery`, `NON_QUERY` batch op | `NonQueryReply`. Over 4 MiB fails with `CADB0550`. |

- For the count only, set `"discardReturningRows": true` (HTTP) or
  `discard_returning_rows = true` (gRPC `ExecuteNonQuery`). The query endpoints refuse
  the flag with `CADB0400`.
- `RETURNING` needs `SELECT` on the target. Without it the statement fails with
  `CADB0517`.
- `returning` is reserved. Quote a column with that name in backticks.

### ON CONFLICT and UPSERT

```sql
INSERT INTO counters (k, n) VALUES ('hits', 1)
  ON CONFLICT (k) DO UPDATE SET n = counters.n + excluded.n
  RETURNING k, n;

INSERT INTO seen (k) VALUES ('a'), ('b') ON CONFLICT DO NOTHING;

UPSERT INTO counters (k, n) VALUES ('hits', 1);
```

The arbiter is the unique index that decides a conflict:

| Target | Arbiter |
| --- | --- |
| `ON CONFLICT (a, b)` | Each unique index on exactly `{a, b}`, in any order. |
| `ON CONFLICT ON CONSTRAINT name` | The unique index with that name. The primary key is `<table>_pkey`. |
| No target (`DO NOTHING` only) | Every unique index, primary key included. |
| `UPSERT` | The primary key. |

- No matching unique index gives `CADB0400`. `DO UPDATE` must name a target.
- A duplicate on a unique index that is not an arbiter still fails, and ends the
  transaction.
- `NULL` keys never conflict, so such a row is always inserted.
- `NOT NULL` and `CHECK` run on every proposed row first, even when the key is taken.
- In `SET` and `WHERE`, a bare `c` or `t.c` is the stored row. `excluded.c` is the
  proposed row, after defaults, coercion, and `BEFORE INSERT` triggers.
- Every `SET` reads the stored row before the update, so `SET a = b, b = a` swaps.
- `SET` cannot assign a primary-key column.
- An uncorrelated subquery runs once. A correlated subquery is refused.
- `DO UPDATE` fails with `CADB0400` when two proposed rows reach one stored row.
- `UPSERT` sets each listed non-key column to `excluded`. An unlisted column keeps its
  stored value on update, and gets its default on insert.
- `RETURNING` returns inserted and updated rows in proposed order. A skipped row
  returns nothing. The row count is inserts plus updates.
- A sequence default still uses a value for a skipped row.
- `DO NOTHING` needs `INSERT`. `DO UPDATE` and `UPSERT` need `INSERT` and `UPDATE`.
- A concurrent writer of the same key fails with retryable `CADB0502` or `CADB0504`.
  `CADB0300` is not retried. See the `camusdb-transactions` skill.
- `/insert` and gRPC `InsertRow` have no upsert form. The clause is SQL only.
- Not supported: `ON DUPLICATE KEY UPDATE`, `MERGE`, `INSERT INTO t AS x`, and
  `ON CONSTRAINT` on a `CHECK` or foreign key. The name given in
  `CONSTRAINT name PRIMARY KEY` is not an arbiter name.
- `UPSERT`, `CONFLICT`, `DO`, `NOTHING`, and `EXCLUDED` are not reserved.

## Copy query results

```sql
INSERT INTO target [(col, ...)] [OVERRIDING { SYSTEM | USER } VALUE] SELECT ...;
CREATE TABLE [IF NOT EXISTS] new_t AS SELECT ... [WITH DATA | WITH NO DATA];
```

- Columns match by position.
- `WITH DATA` is the default. `IF NOT EXISTS` skips the query when the table exists.
- `INSERT INTO t SELECT ... FROM t` terminates; the source is read first.
- CTAS copies the result shape only: no index, constraint, default, or comment.
- CTAS adds a leading key `id OID NOT NULL DEFAULT gen_id()`. If the query has an
  `id` column, the key becomes `id2`, then `id3`.
- In CTAS, alias every expression, cast a bare `NULL` (`CAST(NULL AS INT64) AS x`),
  and list columns instead of `*` over a join.
- CTAS commits the table before the rows. A failed load drops the table again. If
  that drop fails, an empty table stays.
- A source with `AS OF SYSTEM TIME` copies historical rows into the present. It also
  works inside an explicit transaction. It takes no lock and no commit dependency.
- The isolation level and locking mode decide what a live source does:
  - Serializable: an exclusive range lock over the scanned range until commit.
  - Read Committed, pessimistic: no lock and no dependency.
  - Optimistic: every visited row becomes a commit dependency, and a rewrite fails
    the commit with `CADB0502`.
- To copy from a busy table, use `AS OF SYSTEM TIME`, run pessimistically, or copy in
  small slices.
- HTTP endpoints: `POST /execute-sql-ddl` for CTAS,
  `POST /execute-sql-non-query` for `INSERT ... SELECT`. The response has
  `rows` and `warning`. A historical copy that reads nothing sets `warning`.
- Privileges: `Insert` on the target (or `CreateTable` for CTAS) and `Select`
  on every source.

## Views

```sql
CREATE [OR REPLACE] VIEW v [(c1, c2, ...)] AS SELECT ...;
ALTER VIEW v RENAME TO v2;
ALTER VIEW v OWNER TO user_name;               -- superuser or current owner
DROP VIEW v [CASCADE];
SHOW VIEWS [LIKE 'pattern'];
SHOW CREATE VIEW v;
```

- `CREATE OR REPLACE` keeps the owner and may only append columns.
- `SHOW CREATE VIEW` prints a normalized definition that parses again.
- `max_view_expansion_depth` (default 32) caps nesting. A cycle fails with `CADB0528`.
- Names starting with `__camus_rel_` (relations) and `__camus_col_` (columns) are reserved.
- `WITH [LOCAL | CASCADED] CHECK OPTION` parses and is stored, but nothing enforces it.
- A body can be a set operation. A subquery in the body's `WHERE` reads with the
  owner's privileges. A set operation deeper in the body cannot use `*`.
- Every output column needs a name. A body may not mix `*` with other columns.
- A view runs with its owner's privileges. Every view is read-only (`CADB0525`).
- `DROP TABLE` and `DROP COLUMN` fail while a view depends on them (`CADB0530`).
  `DROP VIEW ... CASCADE` exists. `DROP TABLE ... CASCADE` does not.

## Materialized views

```sql
CREATE MATERIALIZED VIEW mv [(c1, ...)] AS SELECT ... [WITH NO DATA];
REFRESH MATERIALIZED VIEW mv [WITH NO DATA];
ALTER MATERIALIZED VIEW mv RENAME TO mv2;
DROP MATERIALIZED VIEW [IF EXISTS] mv [CASCADE];
SHOW MATERIALIZED VIEWS [LIKE 'pattern'];
SHOW CREATE MATERIALIZED VIEW mv;
CREATE INDEX mv_idx ON mv (col);
```

- `WITH NO DATA` makes reads fail with `CADB0531` until a refresh. `CONCURRENTLY`
  fails with `CADB0533`.
- A materialized view gets a generated `id` key. You can index it.
- A refresh rebuilds beside the live copy and swaps atomically. Readers never block.
- One refresh at a time per view, cluster-wide. A second one fails with `CADB0532`.
- The rebuild writes `materialized_view_refresh_chunk_rows` rows per
  transaction (default 10000), so it is not bound by the mutation limit.
- `REFRESH ... WITH NO DATA` empties the view. `TRUNCATE` on it fails.
- Tables, views, and materialized views share one namespace.
- `DROP TABLE` refuses a materialized view. Use `DROP MATERIALIZED VIEW`.

## Databases

```sql
CREATE DATABASE [IF NOT EXISTS] db;
CREATE DATABASE branch_db BRANCH FROM db;
CREATE DATABASE db_recovered RELINK TO '<orphan id>';
DROP DATABASE [IF EXISTS] db [FORCE];
RENAME DATABASE db TO db2;
ALTER DATABASE db RENAME TO db2;
SHOW DATABASES;
SHOW DATABASE;                                 -- current database and its comment
SHOW BRANCHES FROM db;
SHOW ANCESTORS FROM branch_db;
SHOW ORPHAN DATABASES;
```

- `DROP DATABASE` keeps a recoverable orphan for the retention window.
  `FORCE` deletes it permanently.
- A database with live branches cannot be dropped (`CADB0508`).
- Errors: `CADB0010` unknown database, `CADB0012` name exists,
  `CADB0018` reserved name (`_system`, `information_schema`),
  `CADB0510` unknown orphan id.
- `USE db` is a `camus-cli` shell command, not a server statement.

## Inspection

```sql
SHOW TABLES;
SHOW TABLES FROM db [LIKE 'pattern'];           -- also SHOW TABLES IN db
SHOW COLUMNS FROM t;    DESCRIBE t;    DESC t;
SHOW CREATE TABLE t;
SHOW INDEXES FROM t;    SHOW INDEX FROM t;
SHOW ORPHAN TABLES;
SHOW GRANTS;            SHOW GRANTS FOR user_name;
SHOW STATISTICS FOR t;  ANALYZE t;
EXPLAIN SELECT ...;     EXPLAIN (ANALYZE) SELECT ...;
SHOW RANGES FROM TABLE t;
```

- `SHOW TABLES` lists tables only. Use `SHOW VIEWS` and `SHOW MATERIALIZED VIEWS`.
- `SHOW TABLES FROM db` works when the current database is missing or not set. A
  database the caller cannot see fails with `CADB0010`.
- `SHOW COLUMNS` and `DESCRIBE` work on views. `SHOW INDEXES` works on a materialized view.
- `SHOW CREATE TABLE` renders comments, covering-index `INCLUDE`, and foreign keys.
  `SHOW COLUMNS` does not show comments.
- Node-level statements (`SHOW VARIABLES`, `SHOW ENGINE STATS`,
  `SHOW SLOW QUERIES`, `SHOW QUERIES`, `SHOW CONNECTIONS`,
  `SHOW CLUSTER SETTINGS`) need no database. Most accept `LIKE '...'`.

## Drop an index

```sql
DROP INDEX [IF EXISTS] idx ON t;
DROP INDEX [IF EXISTS] t.idx;
ALTER TABLE t DROP INDEX [IF EXISTS] idx;
```

The table is required. A bare name fails with `CADB0400`. See the
`camusdb-schema-design` skill.

## Comments on schema objects

```sql
COMMENT ON DATABASE db IS 'text';
COMMENT ON TABLE t IS 'text';
COMMENT ON COLUMN t.col IS 'text';
COMMENT ON INDEX t.idx IS 'text';
COMMENT ON SEQUENCE s IS 'text';
COMMENT ON COLUMN t.col IS NULL;               -- removes the comment
```

- Inline form in DDL: `COMMENT 'text'`, with no `=`.
- `CREATE DATABASE` has no inline comment. Use `COMMENT ON DATABASE`.
- No `COMMENT ON` for a primary-key index, a constraint, a branch, or a function.
- `COMMENT ON COLUMN` and `COMMENT ON INDEX` need a `table.name` target.
- `IS ''` keeps an empty comment. `IS NULL` removes it.
- The limit is 65,535 characters (`CADB0511`).

## Prepared statements

Prepare through a transport or driver. There is no SQL `PREPARE` statement.

- gRPC: on the duplex `BatchExecute` stream, `PREPARE` → `statement_id` and
  `parameter_names`; execute with `positional_parameters`; `CLOSE`. A handle
  lives as long as the stream.
- REST: `POST /prepare-sql-statement` → `statementId`, `parameterNames`.
  Execute on `/execute-sql-query`, `/execute-sql-query-stream`, or
  `/execute-sql-non-query` with `statementId` and `positionalParameters`. Omit
  `sql`, `databaseName`, and `parameters`. Close with `POST /close-sql-statement`.
- A handle belongs to one node (and, for REST, one principal). It is never replicated.
- The .NET and TypeScript connectors bind by name and send ordinals for you.
- Preparable: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, every `SHOW`.

| Key | Default |
| --- | ---: |
| `grpc_max_prepared_statements_per_stream` | 512 |
| `rest_max_prepared_statements_per_principal` | 512 |
| `rest_max_prepared_statements` | 8192 |
| `max_prepared_statement_bytes` | 65536 |
| `grpc_max_prepared_statement_bytes_per_stream` | 8 MiB |
| `rest_max_prepared_statement_bytes_per_principal` | 8 MiB |
| `rest_max_prepared_statement_bytes` | 64 MiB |
| `prepared_statement_idle_timeout_ms` | 600000 |
| `prepared_statement_sweep_interval_ms` | 60000 |

Errors: `CADB0520` `UnknownPreparedStatement` (HTTP 404): prepare again and
replay once. `CADB0521` `PreparedStatementLimitExceeded` (HTTP 429): close
unused handles.

## Error codes in this skill's scope

| Code | Name | Typical cause |
| --- | --- | --- |
| `CADB0010` | `DatabaseDoesntExist` | Unknown database, or no grant on it. |
| `CADB0011` | `TableDoesntExist` | Unknown table. |
| `CADB0302` | `ValueTooLong` | Value exceeds `STRING(N)` or `BYTES(N)`. |
| `CADB0400` | `InvalidInput` | Bad shape: CTAS projection, `INSERT ... SELECT` count, bad cast, bad regex, `RETURNING` aggregate. A comparison across types with no rule. No common type in `CASE`, `COALESCE`, `IFNULL`, `IF`, or a set operation. A window call in a bad clause. |
| `CADB0403` | `DuplicateColumn` | A repeated column in `UPDATE ... SET`, an `INSERT` target list, or `ON CONFLICT DO UPDATE`. |
| `CADB0404` | `UnknownColumn` | Unknown column name. |
| `CADB0406` | `SqlSyntaxError` | The parser cannot parse the text. |
| `CADB0413` | `StatementTooDeeplyNested` | Parse tree deeper than 1,000 levels, or more than 64 nested set operations. |
| `CADB0417` | `NumericValueOutOfRange` | `NUMERIC` overflow. |
| `CADB0418` | `InvalidJsonText` | Text is not a valid `jsonb` document. |
| `CADB0419` | `JsonNestingTooDeep` | A document nests deeper than 512 levels. |
| `CADB0420` | `IntegerValueOutOfRange` | `INT64` arithmetic, `SUM`, a literal, or a cast past the range. |
| `CADB0421` | `FloatValueOutOfRange` | Float overflow or underflow from finite values. |
| `CADB0422` | `JsonbValueTooLarge` | A built `jsonb` aggregate exceeds 268,435,455 bytes. |
| `CADB0506` | `TransactionMutationLimitExceeded` | More than 20,000 mutations by default. |
| `CADB0511` | `CommentTooLong` | Comment over 65,535 characters. |
| `CADB0520` | `UnknownPreparedStatement` | Handle expired or unknown on this node. |
| `CADB0521` | `PreparedStatementLimitExceeded` | Prepared-statement cap or budget reached. |
| `CADB0523` | `ViewDoesntExist` | Unknown view or materialized view. |
| `CADB0525` | `ViewNotUpdatable` | DML or `TRUNCATE` on a view or materialized view. |
| `CADB0528` | `ViewRecursionDetected` | View dependency cycle. |
| `CADB0529` | `CannotChangeViewShape` | `CREATE OR REPLACE VIEW` changed existing columns. |
| `CADB0530` | `DependentObjectsExist` | Drop or truncate blocked by a view or foreign key. |
| `CADB0531` | `MaterializedViewNotPopulated` | Read of a `WITH NO DATA` materialized view. |
| `CADB0532` | `RefreshAlreadyInProgress` | A second concurrent refresh. |
| `CADB0533` | `FeatureNotSupported` | Parsed but unimplemented: `REFRESH ... CONCURRENTLY`, `FULL JOIN`, `FOR SHARE`, a correlated `EXISTS` outside the `WHERE` of a `SELECT`, `LATERAL`, or a set-returning function in the select list. |
| `CADB0534` | `ConcurrentSchemaChange` | Lost a schema race. Retryable. |
| `CADB0537` | `SnapshotPrecedesContentsGeneration` | `AS OF SYSTEM TIME` before the last `TRUNCATE`. |
| `CADB0538` | `StatementNotAllowedInTransaction` | `TRUNCATE` inside an explicit transaction. |
| `CADB0547` | `SequenceCallNotAllowedHere` | Sequence call in `RETURNING` or a similar place. |
| `CADB0550` | `ReturningResultTooLarge` | `RETURNING` rows exceed one gRPC reply (4 MiB). |
| `CADB0555` | `LockNotAvailable` | `SELECT ... FOR UPDATE NOWAIT` met a held row. |
| `CADB0556` | `ResultSetTooLarge` | A buffered result passed `max_buffered_result_rows` or `max_buffered_result_bytes`. Stream or page it. |
| `CADB0557` | `StatementTooLarge` | Inline SQL over `max_statement_bytes` (default 8 MiB). Bind large values as parameters. |
| `CADB0559` | `JsonbValueCorrupt` | A stored `jsonb` cell does not decode. |

Full list: https://camusdb.github.io/docs/error-codes.
