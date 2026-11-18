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
| `INT64` | `42`, `200_000`, `0xFF`; past the range fails with `CADB0420`. `SMALLINT` and `INT` columns check their narrower range. |
| `NUMERIC` | `NUMERIC '0.1'` (typed); a bare decimal is a float in expressions |
| `FLOAT64` | `3.14`, `1e10`, `CAST('NaN' AS float64)`; `1e400` fails with `CADB0421` |
| `STRING` | `'hello'` or `"hello"`; `E'a\tb'` for escapes |
| `BOOL` | `true`, `false` |
| `OID` | `'507f1f77bcf86cd799439011'` (24 hex chars), or `str_id('...')` |
| `UUID` | `'550e8400-e29b-41d4-a716-446655440000'`, or 32 hex digits |
| `DATE` | `'2026-03-15'`, `DATE '2026-03-15'` |
| `DATETIME` | `'2026-03-15T12:00:00Z'` (UTC, ISO-8601), `TIMESTAMP '...'`, `DATETIME '...'` |
| `INTERVAL` | `INTERVAL '7 days'`, `INTERVAL 3 HOUR`, `'1 day'::interval`. Expression only; see "Intervals" in [functions.md](functions.md). |
| `BYTES` | `X'DEADBEEF'` or `x'...'`, an even digit count |
| `ARRAY(T)` | `ARRAY[1, 2, 3]`, `ARRAY[]` |
| `JSONB` | `JSONB '{"a": 1}'`, `'{"a": 1}'::jsonb` |

CamusDB parses date and datetime text as UTC. The date functions reject a
timestamp string with no `Z` or offset, such as `'2024-06-15 10:30:00'`.

A typed literal (`DATE '...'`, `TIMESTAMP '...'`, `DATETIME '...'`) is the same as
a cast of its text. It gives an expression its type without a target column. Put
`T` between the date and the time: `TIMESTAMP '2026-03-15 12:00:00'` is refused.
In a column default, wrap it in parentheses: `DEFAULT (DATE '2026-03-15')`.

## Queries

```sql
[WITH [RECURSIVE] name [(col, ...)] AS [[NOT] MATERIALIZED] (query) [, ...]]
SELECT [DISTINCT] expr [AS alias] [, ...]
FROM source
  [AS OF SYSTEM TIME '-10s' | '<timestamp>' | <epoch-ms> | @p]
[WHERE condition]
[GROUP BY expr [, ...]]
[HAVING condition]
[ORDER BY expr [ASC | DESC] [, ...]]
[LIMIT n | @p] [OFFSET m | @p]
[FOR UPDATE [OF t] [NOWAIT | SKIP LOCKED]];

-- SELECT without FROM: one synthetic row
SELECT expr [AS alias] [, ...] [WHERE condition] [LIMIT n] [OFFSET m];
```

`SELECT` without `FROM`:

- `WHERE` keeps the row when the condition is `TRUE`. `FALSE` or `NULL` returns no
  row, and the projection does not run. The condition must be boolean or `NULL`.
- It refuses `*`, aggregates, `GROUP BY`, `HAVING`, and `ORDER BY`, also with `WHERE`.
- With `WHERE`, it can be the source of `INSERT ... SELECT` or CTAS, and an
  operand of a set operation. `EXPLAIN (ANALYZE)` works and shows a
  `function-scan` of `*single_row*`. Without `WHERE`, `EXPLAIN (ANALYZE)` is refused.
- It also works nested: as a scalar or `IN` subquery, a derived table, or a view
  body. There it can read outer columns in a supported correlated position.
- With `WHERE`, a sequence call fails with `CADB0547`, unless the filter drops the
  row before the call runs: `SELECT nextval('s') WHERE FALSE` draws nothing. A
  nested `SELECT` without `FROM` refuses a sequence call with `CADB0547`.
- A constant division by zero still fails: `SELECT 1 / 0 WHERE FALSE` is an error.

```sql
SELECT 'seed' WHERE NOT EXISTS (SELECT 1 FROM accounts);
SELECT (SELECT 1 WHERE FALSE);             -- NULL
SELECT x FROM (SELECT 5 AS x) d;           -- 5
SELECT u.id FROM app_users u WHERE EXISTS (SELECT 1 WHERE u.active = true);
```

`source` is one of:

```sql
table [[AS] alias]
table@{FORCE_INDEX=index_name}
cte_name [[AS] alias]                           -- a WITH definition
(SELECT ...) [AS] alias [(col, ...)]            -- derived table, alias required
(VALUES (v1, v2), (...)) AS alias [(col, ...)]  -- inline rows, alias required
a [INNER] JOIN b ON cond
a LEFT [OUTER] JOIN b ON cond
a RIGHT [OUTER] JOIN b ON cond                  -- left operand must not be a join
a CROSS JOIN b
a, b [, ...]                                    -- comma join; equalities in WHERE become join keys
a CROSS JOIN LATERAL (SELECT ...) AS x          -- also comma, [INNER] JOIN, LEFT JOIN ... ON true
fn(args) [[AS] alias[(col)]]                    -- set-returning function: generate_series, jsonb_each
```

`FULL [OUTER] JOIN` and a `RIGHT JOIN` after another join fail with `CADB0533`.
`SELECT *` over a `RIGHT JOIN` lists the preserved table's columns first.
`WITH`, `LATERAL`, `VALUES` sources, and derived-table column lists are in
[ctes-and-lateral.md](ctes-and-lateral.md). A table-function argument can read
columns of sources to its left; the function then runs for each left row.

Predicates:

```sql
x [NOT] BETWEEN lo AND hi
x [NOT] IN (v1, @p, column_name)                 -- an expression item is a syntax error
x [NOT] IN (SELECT one_column FROM ...)          -- correlated or uncorrelated
[NOT] EXISTS (SELECT ... )
x op ANY | SOME | ALL (array_or_subquery)        -- op: = <> != < <= > >=
x [NOT] LIKE p [ESCAPE e] | x [NOT] ILIKE p [ESCAPE e]
x ~ p | x ~* p | x !~ p | x !~* p
x IS NULL | x IS NOT NULL
CASE WHEN c THEN r [...] [ELSE r] END
CASE x WHEN v THEN r [...] [ELSE r] END
IF(c, a, b)
```

- A column in an `IN` list reads the same row as `x`. Only a list of literals or
  parameters can become index seeks.
- `HAVING` reads a group key, a select item, or an aggregate. A qualified key works,
  and so does `COUNT(*) IN (1, 2)`.
- `WHERE`, `HAVING`, and `JOIN ON` need a boolean or `NULL` condition.
- `LIKE` rules, `ESCAPE`, and operator precedence are in
  [expressions.md](expressions.md).

### Subqueries

An uncorrelated subquery does not read the outer row. It runs once, before the
outer rows are read. A correlated subquery reads an outer column and runs for
each outer row.

| Position | Supported subqueries |
| --- | --- |
| `SELECT` list (no grouping, aggregates, or window functions), `SELECT ... WHERE` | Scalar, `EXISTS`, `IN`/`NOT IN`, quantified. Correlated or uncorrelated. |
| `UPDATE ... SET`/`WHERE`, `DELETE ... WHERE` | The same forms. The outer scope is the target row. |
| `HAVING`, `JOIN ON`, `INSERT ... VALUES`, `ON CONFLICT DO UPDATE` | Uncorrelated forms only. |
| `GROUP BY`, `ORDER BY` | Refused with `CADB0400`. |

- The rules also apply inside a `CASE` branch, a function argument, a comparison,
  an `IS NULL` test, or a `BETWEEN` bound. Limits on `CHECK` and `RETURNING` stay.
- A correlated subquery in `HAVING`, `JOIN ON`, `ON CONFLICT DO UPDATE`, or a
  grouped, aggregate, or windowed select list fails with `CADB0533`.
- A scalar subquery gives `NULL` for zero rows. More than one row is an error. It
  takes its inner output name. Alias it anyway for callers and stored definitions.
- `IN` and `NOT IN` need a one-column subquery. A multi-column one is refused.
- A scalar, `IN`, `ANY`, or `ALL` subquery accepts `SELECT *` or `SELECT alias.*`
  when it expands to exactly one column. More than one column is refused.
- An empty inner result makes `NOT IN` true, even for a `NULL` outer value. One
  `NULL` in a non-empty result makes every non-match unknown. Prefer `NOT EXISTS`.
- A name resolves in the nearest query first, then in the enclosing queries. An
  inner column or alias can hide an outer name. Qualify columns.
- In `UPDATE` and `DELETE`, a correlated body reads at the statement timestamp. It
  does not see changes made earlier by the same statement.
- A correlated value error is raised only when the expression uses that value. An
  unused `CASE` branch or `COALESCE` fallback can skip it. Name and known type
  errors still fail before rows are read.
- An `EXISTS` body drops its select list, `DISTINCT`, `GROUP BY`, and `ORDER BY`:
  `EXISTS (SELECT 1 / 0 FROM posts)` does not divide. Unknown names and known type
  mistakes there still fail. Aggregates, `HAVING`, `OFFSET`, and `LIMIT 0` keep
  their meaning. `LIMIT 0` is false without a read.
- A correlated `EXISTS` body can use joins, views, derived tables, set operations,
  aggregates, `GROUP BY`, `HAVING`, window functions, `OFFSET`, and nested
  subqueries. It can omit `FROM` and read only outer values.
- In a correlated `EXISTS` body, an outer name inside a table-function argument is
  not supported. A column of a source to the left in the same `FROM` works.
- A simple one-table `EXISTS` can seek an index on the correlated columns. Other
  bodies run the full query pipeline. A result can be reused for repeated outer
  values within one statement. See `camusdb-query-performance`.

```sql
SELECT u.id,
       (SELECT COUNT(*) FROM posts p WHERE p.user_id = u.id) AS post_count,
       EXISTS (SELECT 1 FROM posts p WHERE p.user_id = u.id) AS has_posts
FROM app_users u;

UPDATE app_users
SET post_count = (SELECT COUNT(*) FROM posts p WHERE p.user_id = app_users.id)
WHERE active = true;
```

`AS OF SYSTEM TIME` reads a past snapshot. A plain read with it runs in
autocommit only. It applies to every join and subquery of the statement. When
retention has removed history that the read needs, it fails with `CADB0847`
`SnapshotTooOld` (HTTP 409). A retry at the same timestamp cannot succeed: choose a
newer timestamp, or begin a new transaction. See
https://camusdb.github.io/docs/time-travel-reads.

`UNION`, `INTERSECT`, `EXCEPT`, window functions (`OVER`), and `generate_series` are
in [set-and-window.md](set-and-window.md). `WITH`, `WITH RECURSIVE`, `LATERAL`, and
`VALUES` sources are in [ctes-and-lateral.md](ctes-and-lateral.md).

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
UPDATE t [AS a] SET col = expr [, ...] [FROM sources] WHERE condition [RETURNING list];
DELETE FROM t [AS a] [USING sources] WHERE condition [RETURNING list];
TRUNCATE [TABLE] t [RESTART IDENTITY | CONTINUE IDENTITY];
```

A `WITH` clause can precede `INSERT`, `UPSERT`, `UPDATE`, and `DELETE`. See
[ctes-and-lateral.md](ctes-and-lateral.md).

`SHOW ORPHAN TABLES` and `CREATE TABLE t2 RELINK TO '<id>'` recover truncated rows
within the retention window.

- Without a column list, `INSERT` targets every column in schema order.
- `WHERE` is required in `UPDATE` and `DELETE`.
- `RETURNING list` is a select list over the target table: `*`, columns,
  scalar expressions, `AS` aliases, parameters. A joined write refuses a bare `*`.
- `RETURNING` returns the stored row image: the inserted row, the new row after
  `UPDATE`, or the deleted row. `OLD` and `NEW` qualifiers are not supported.
- A repeated target column fails with `CADB0403` before any write. Names compare
  without case. `SET v = 1, v = 2` and `(id, v, V)` both fail.
- Any failed statement ends an explicit transaction. There are no savepoints.
- `CONTINUE IDENTITY` is the default. `RESTART IDENTITY` restarts only
  sequences owned by identity columns of the table.

### UPDATE ... FROM and DELETE ... USING

```sql
UPDATE accounts AS a
SET balance = a.balance + d.amount
FROM deltas AS d
WHERE d.account_id = a.id
RETURNING a.id, a.balance;

DELETE FROM orders AS o
USING customers AS c
WHERE c.id = o.customer_id AND c.closed = true
RETURNING o.id;

UPDATE accounts AS a SET balance = a.balance + d.amount
FROM (VALUES (1, 25), (2, -10)) AS d (id, amount)
WHERE a.id = d.id;
```

- Only the target table changes. `FROM` and `USING` take the sources of a
  `SELECT`: tables, views, derived tables, CTEs, `VALUES` lists, set-returning
  functions, comma lists, joins, and lateral sources.
- A target alias also works without a source list:
  `UPDATE accounts AS a SET balance = 0 WHERE a.id = @id`. Once you alias the
  target, qualify it by the alias.
- A bare column that the target and a source share is ambiguous (`CADB0400`). A
  self join needs an alias on at least one copy of the target.
- The statement first collects the matching target keys and source values. A self
  join reads rows as this match phase found them. Then it locks and rereads each
  target row. `SET` reads the locked target values and the collected source values,
  so `a.balance + d.amount` adds the delta to the balance read under the lock.
- Every assignment reads the same row before the update. A subquery in `SET` runs in
  the match phase.
- After the lock, each `AND` term that reads a target column and holds no subquery
  is checked again. A row that fails it now is skipped and not counted.
- Triggers, constraints, index upkeep, and transaction limits apply as for a plain
  write. Under Serializable, the match phase takes exclusive range locks on the
  target and source relations. The write phase takes exclusive locks on target rows.
- An `UPDATE` fails with `CADB0400`, before it writes anything, when one target row
  matches several source rows. PostgreSQL picks one match. Aggregate the source
  first. A `DELETE` removes such a row once.

```sql
UPDATE accounts AS a
SET balance = a.balance + d.amount
FROM (SELECT account_id, SUM(amount) AS amount FROM deltas GROUP BY account_id) AS d
WHERE d.account_id = a.id;
```

Limits:

- `WHERE` is required. Write `WHERE TRUE` to change every matching row.
- `LIMIT` is not supported in these forms. A plain `UPDATE` and `DELETE` keep it.
- `RETURNING` takes target columns and target expressions only. A source column or
  a bare `RETURNING *` fails with `CADB0400`. A target alias without `FROM` or
  `USING` still allows `RETURNING *`.
- A primary-key assignment, `EXPLAIN UPDATE`, and `EXPLAIN DELETE` are not supported
  in these forms.
- Privileges: `UPDATE` or `DELETE` on the target, and `SELECT` on every relation
  the statement reads, the target included.
- `USING` is reserved. Quote a name `using` with backticks. `using_id` is fine.

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
- The .NET `ExecuteStreamReaderAsync()` and TypeScript `queryStream()` stream the
  rows. The .NET `ExecuteNonQueryAsync()` and TypeScript `execute()` ask for the
  count only. Request fields: https://camusdb.github.io/docs/http-api#post-execute-sql-non-query
  and https://camusdb.github.io/docs/grpc-api#sql-service.
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
- An uncorrelated subquery runs once. A correlated subquery is refused (`CADB0533`).
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
- The source is an ordinary `SELECT`. It can read a plain view, also for CTAS
  `WITH NO DATA`. Reading a view does not make the view writable.
- The source can use `WITH`, `LATERAL`, and `VALUES` sources:
  `INSERT INTO t WITH c AS (...) SELECT ... FROM c`.
- A scalar subquery keeps its declared type even when its value is `NULL`. So
  `SELECT (SELECT MAX(amount) FROM payments) AS largest_payment` makes a typed column
  over an empty table. An untyped `SELECT NULL AS x` is still refused.
- `WITH DATA` is the default. `IF NOT EXISTS` skips the query when the table exists.
- `INSERT INTO t SELECT ... FROM t` terminates; the source is read first.
- CTAS copies the result shape only: no index, constraint, default, or comment.
- CTAS adds a leading key `id OID NOT NULL DEFAULT gen_id()`. If the query has an
  `id` column, the key becomes `id2`, then `id3`.
- In CTAS, alias every expression, cast a bare `NULL` (`CAST(NULL AS INT64) AS x`),
  and list columns instead of `*` over a join. A scalar subquery can take its inner
  name, but an expression such as `MAX` inside it still needs an alias.
- CTAS makes `INT64` integer columns, also from a `SMALLINT` or `INT` source. It
  cannot store an `INTERVAL` column: cast it to `STRING`.
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
- `CREATE VIEW` binds the body without running it or its subqueries.
  `CREATE VIEW deferred AS SELECT (SELECT 1 / 0) AS x` succeeds, and a read of it
  fails. Unknown names and known type mistakes still fail at creation.
- A body can be a `SELECT` without `FROM`: `CREATE VIEW readiness AS SELECT 'ready' AS status`.
- A body can use `WITH` and `WITH RECURSIVE`. The stored body expands each CTE
  reference into a derived table, and `SHOW CREATE VIEW` shows them. Each reference
  then runs separately: the computed-once reuse is lost. A recursive CTE keeps
  `WITH RECURSIVE` inside a derived-table wrapper. `CREATE RECURSIVE VIEW` is not
  supported.
- A view can be the source of CTAS, `INSERT ... SELECT`, or a materialized view. It
  reads with the rules of an ordinary `SELECT`.
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
- The body can use `WITH` as a view body does. A refresh stores its result as usual.
  An `INTERVAL` column cannot be stored.
- The body can read a plain view. A scalar-subquery column keeps its declared type
  when the subquery has no value. The definition binds without running subqueries.
  The population step then runs the query, unless `WITH NO DATA` is given.
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

- The .NET and TypeScript drivers prepare repeated statements for you, manage the
  handles, and recover expired ones. `CamusCommand.PrepareAsync()` and
  `client.prepare(sql)` prepare before the first run. The Python driver binds
  parameters without server-side prepared statements.
- gRPC: on the duplex `BatchExecute` stream, `PREPARE` → `statement_id` and
  `parameter_names`; execute with `positional_parameters`; `CLOSE`. A handle
  lives as long as the stream.
- REST: `POST /prepare-sql-statement` → `statementId`, `parameterNames`.
  Execute on `/execute-sql-query`, `/execute-sql-query-stream`, or
  `/execute-sql-non-query` with `statementId` and `positionalParameters`. Omit
  `sql`, `databaseName`, and `parameters`. Close with `POST /close-sql-statement`.
- A handle belongs to one node (and, for REST, one principal). It is never replicated.
- Preparable: `SELECT` (also `SELECT ... FOR UPDATE`), `INSERT`, `UPDATE`, `DELETE`,
  every `SHOW`. A prepare checks no privileges; the execution does.
- Protocol details for a custom client:
  https://camusdb.github.io/docs/http-api#prepared-statements and
  https://camusdb.github.io/docs/grpc-api#prepared-statements.

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
| `CADB0303` | `CheckConstraintViolation` | A type error while an existing `CHECK` runs rejects the write. A new `CHECK` with a known type error fails at definition. |
| `CADB0400` | `InvalidInput` | Bad shape: CTAS projection, `INSERT ... SELECT` count, bad cast, bad regex, `RETURNING` aggregate. A comparison across types with no rule. No common type in `CASE`, `COALESCE`, `IFNULL`, `IF`, or a set operation. A window call in a bad clause. A wrong argument count. A non-boolean `WHERE`, `HAVING`, or `JOIN ON`. A constant division by zero. A negative `LIMIT` or `OFFSET`. An ambiguous `ORDER BY` name. A bad `LIKE` escape. A lone surrogate half in a string parameter. Malformed JSON on a SQL endpoint. In `UPDATE ... FROM`, a target row that matches several source rows. In a joined write, an ambiguous bare column, a bare `RETURNING *`, or a source column in `RETURNING`. An interval overflow. An infinite angle, or an inverse sine or cosine input outside `[-1, 1]`. |
| `CADB0401` | `UnknownType` | A known invalid assignment, refused before any row is read. |
| `CADB0403` | `DuplicateColumn` | A repeated column in `UPDATE ... SET`, an `INSERT` target list, or `ON CONFLICT DO UPDATE`. |
| `CADB0404` | `UnknownColumn` | Unknown column name. |
| `CADB0406` | `SqlSyntaxError` | The parser cannot parse the text. Also an unknown character, a non-SQL white space, a second statement in one request, or a chain such as `a = b = c`. The message gives the line and column. |
| `CADB0407` | `InvalidAstStmt` | An unknown function, refused before any row is read. |
| `CADB0413` | `StatementTooDeeplyNested` | Parse tree deeper than 1,000 levels, more than 64 nested set operations, or a `WITH` clause whose expansion copies more than 250,000 parse nodes. |
| `CADB0417` | `NumericValueOutOfRange` | `NUMERIC` overflow. |
| `CADB0418` | `InvalidJsonText` | Text is not a valid `jsonb` document. |
| `CADB0419` | `JsonNestingTooDeep` | A document nests deeper than 512 levels. |
| `CADB0420` | `IntegerValueOutOfRange` | `INT64` arithmetic, `SUM`, a literal, or a cast past the range. A value outside a declared `SMALLINT` or `INT` range. |
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
| `CADB0533` | `FeatureNotSupported` | Parsed but unimplemented: `REFRESH ... CONCURRENTLY`, `FULL JOIN`, `FOR SHARE`, a correlated subquery in `HAVING`, `JOIN ON`, `ON CONFLICT DO UPDATE`, or a grouped select list, or a set-returning function in the select list. |
| `CADB0534` | `ConcurrentSchemaChange` | Lost a schema race. Retryable. |
| `CADB0537` | `SnapshotPrecedesContentsGeneration` | `AS OF SYSTEM TIME` before the last `TRUNCATE`. |
| `CADB0538` | `StatementNotAllowedInTransaction` | `TRUNCATE` inside an explicit transaction. |
| `CADB0547` | `SequenceCallNotAllowedHere` | Sequence call in `RETURNING`, in a nested `SELECT` without `FROM`, or in a filtered one. |
| `CADB0550` | `ReturningResultTooLarge` | `RETURNING` rows exceed one gRPC reply (4 MiB). |
| `CADB0555` | `LockNotAvailable` | `SELECT ... FOR UPDATE NOWAIT` met a held row. |
| `CADB0556` | `ResultSetTooLarge` | A buffered result passed `max_buffered_result_rows` or `max_buffered_result_bytes`. Stream or page it. |
| `CADB0557` | `StatementTooLarge` | Inline SQL over `max_statement_bytes` (default 8 MiB). Bind large values as parameters. |
| `CADB0559` | `JsonbValueCorrupt` | A stored `jsonb` cell does not decode. |
| `CADB0560` | `RecursiveQueryLimitExceeded` | A recursive CTE passed `max_recursive_cte_iterations` (default 10,000 steps) or `max_recursive_cte_rows` (default 10,000,000 rows). HTTP 400. Add a stop condition. |
| `CADB0847` | `SnapshotTooOld` | Retention removed history that a snapshot read needs. HTTP 409. Choose a newer timestamp or begin a new transaction; do not retry the same snapshot. |

Full list: https://camusdb.github.io/docs/error-codes.
