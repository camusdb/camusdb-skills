# CamusDB statement syntax summary

Read this file when you need the exact form of a statement. Square brackets
mark optional parts. `|` separates alternatives. Schema DDL (`CREATE TABLE`
columns, `ALTER TABLE`, `CREATE INDEX`, sequences) belongs to the
`camusdb-schema-design` skill. `BEGIN`, `COMMIT`, and isolation belong to the
`camusdb-transactions` skill. `EXPLAIN` and hints belong to the
`camusdb-query-performance` skill.

## Queries

```sql
SELECT [DISTINCT] expr [AS alias] [, ...]
FROM source
  [AS OF SYSTEM TIME '-10s' | '<timestamp>' | <epoch-ms> | @p]
[WHERE condition]
[GROUP BY expr [, ...]]
[HAVING condition]
[ORDER BY expr [ASC | DESC] [, ...]]
[LIMIT n | @p] [OFFSET m | @p];

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
```

Predicates:

```sql
x BETWEEN lo AND hi
x [NOT] IN (v1, v2, @p)
x [NOT] IN (SELECT one_column FROM ...)          -- not correlated
[NOT] EXISTS (SELECT ... )                       -- may be correlated
x op ANY | SOME | ALL (array_or_subquery)        -- op: = <> != < <= > >=
x LIKE p | x ILIKE p | x ~ p | x ~* p | x !~ p | x !~* p
x IS NULL | x IS NOT NULL
CASE WHEN c THEN r [...] [ELSE r] END
CASE x WHEN v THEN r [...] [ELSE r] END
```

`AS OF SYSTEM TIME` reads a past snapshot. A plain read with it runs in
autocommit only. It applies to every join and subquery of the statement. See
https://camusdb.github.io/docs/time-travel-reads.

## Writes

```sql
INSERT INTO t [(col, ...)] VALUES (expr | DEFAULT, ...) [, (...)] [RETURNING list];
INSERT INTO t [(col, ...)] SELECT ... [RETURNING list];
UPDATE t SET col = expr [, ...] WHERE condition [LIMIT n] [RETURNING list];
DELETE FROM t WHERE condition [LIMIT n] [RETURNING list];
TRUNCATE [TABLE] t [RESTART IDENTITY | CONTINUE IDENTITY];
```

- Without a column list, `INSERT` targets every column in schema order.
- `WHERE` is required in `UPDATE` and `DELETE`.
- `RETURNING list` is a select list over the target table: `*`, columns,
  scalar expressions, `AS` aliases, parameters.
- `RETURNING` returns the stored row image: the inserted row, the new row after
  `UPDATE`, or the deleted row.
- `CONTINUE IDENTITY` is the default. `RESTART IDENTITY` restarts only
  sequences owned by identity columns of the table.

## Copy query results

```sql
INSERT INTO target [(col, ...)] SELECT ...;
CREATE TABLE [IF NOT EXISTS] new_t AS SELECT ... [WITH DATA | WITH NO DATA];
```

- Columns match by position.
- `WITH DATA` is the default. `IF NOT EXISTS` skips the query when the table exists.
- `INSERT INTO t SELECT ... FROM t` terminates; the source is read first.
- A live source holds range locks until commit. An `AS OF SYSTEM TIME` source
  takes no range lock.
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
- `SHOW COLUMNS` and `DESCRIBE` work on views. `SHOW INDEXES` works on a materialized view.
- `SHOW CREATE TABLE` renders comments, covering-index `INCLUDE`, and foreign keys.
- Node-level statements (`SHOW VARIABLES`, `SHOW ENGINE STATS`,
  `SHOW SLOW QUERIES`, `SHOW QUERIES`, `SHOW CONNECTIONS`,
  `SHOW CLUSTER SETTINGS`) need no database. Most accept `LIKE '...'`.

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
| `CADB0400` | `InvalidInput` | Bad shape: CTAS projection, `INSERT ... SELECT` count, bad cast, bad regex, `RETURNING` aggregate. |
| `CADB0404` | `UnknownColumn` | Unknown column name. |
| `CADB0406` | `SqlSyntaxError` | The parser cannot parse the text. |
| `CADB0413` | `StatementTooDeeplyNested` | Parse tree deeper than 1,000 levels. |
| `CADB0417` | `NumericValueOutOfRange` | `NUMERIC` overflow. |
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
| `CADB0533` | `FeatureNotSupported` | Parsed but unimplemented, such as `REFRESH ... CONCURRENTLY`. |
| `CADB0534` | `ConcurrentSchemaChange` | Lost a schema race. Retryable. |
| `CADB0537` | `SnapshotPrecedesContentsGeneration` | `AS OF SYSTEM TIME` before the last `TRUNCATE`. |
| `CADB0538` | `StatementNotAllowedInTransaction` | `TRUNCATE` inside an explicit transaction. |
| `CADB0547` | `SequenceCallNotAllowedHere` | Sequence call in `RETURNING` or a similar place. |
| `CADB0550` | `ReturningResultTooLarge` | `RETURNING` rows exceed one gRPC reply (4 MiB). |

Full list: https://camusdb.github.io/docs/error-codes.
