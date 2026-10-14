---
name: camusdb-sql
description: Writes, ports, and reviews CamusDB SQL (the CamusSQL dialect). Covers SELECT, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT/OFFSET, DISTINCT, CASE, joins, IN/EXISTS subqueries, derived tables, INSERT, UPDATE, DELETE, RETURNING, INSERT ... SELECT, CREATE TABLE ... AS SELECT, TRUNCATE, CREATE VIEW, CREATE MATERIALIZED VIEW, REFRESH MATERIALIZED VIEW, CREATE/DROP/RENAME DATABASE, SHOW TABLES, SHOW COLUMNS, SHOW INDEXES, SHOW CREATE TABLE, DESCRIBE, COMMENT ON, @name placeholders, prepared statements, backtick identifiers, literals, CAST and ::, ANY/ALL, and scalar functions such as gen_id, str_id, gen_uuid_v7, date_add, date_trunc, to_char, date_format, json_value, regexp_replace, coalesce, ifnull, encode, and sha256. Use it when writing queries for CamusDB, porting SQL from PostgreSQL, MySQL, CockroachDB, or Spanner, or fixing errors such as CADB0400, CADB0404, CADB0406, CADB0413, CADB0506, CADB0520, CADB0525, CADB0529, CADB0531, CADB0538, or CADB0550.
---

# CamusDB SQL

CamusDB speaks a compact SQL dialect. Most of it looks like PostgreSQL, with
some MySQL and Spanner forms. Many common PostgreSQL and MySQL features are
missing, and some familiar syntax means something else. Use this skill to emit
SQL that CamusDB accepts, and to port SQL from another engine.

Other skills cover nearby topics. Point to them instead of going deep:

- `camusdb-schema-design`: `CREATE TABLE`, types, keys, indexes, constraints, sequences.
- `camusdb-transactions`: `BEGIN`, isolation, locks, retries, transaction limits.
- `camusdb-query-performance`: `EXPLAIN`, plans, statistics, hints, the result cache.

## Workflow

1. Identify the statement family. Read
   [references/statements.md](references/statements.md) for its exact syntax.
2. Apply the lexical rules below. Check every quote, identifier, and placeholder.
3. Port SQL from another engine? Read
   [references/porting-from-postgres-mysql.md](references/porting-from-postgres-mysql.md)
   and rewrite each construct it lists.
4. Check every function name against
   [references/functions.md](references/functions.md). Never assume that a
   PostgreSQL or MySQL function exists.
5. Check each statement against "Not supported" below. Rewrite, do not hope.
6. For `UPDATE` and `DELETE`, confirm a `WHERE` clause. For large writes, check
   the 20,000-mutation default limit.
7. Verify a non-trivial read with `EXPLAIN`, and a view with `SHOW CREATE VIEW`.

## Lexical rules

- A keyword is not case-sensitive. A function name is not case-sensitive.
- CamusDB stores a name in its creation case. A later match ignores case.
  `Robots` and `robots` cannot both exist in one database.
- Quote an identifier with backticks only. Do this for reserved words, type
  names, and function names. `CASE`, `END`, and `COMMENT` are reserved.
- `'...'` and `"..."` are both **string literals**. A double quote never quotes
  an identifier.
- A plain string does not treat a backslash as an escape. Double the active
  quote to escape it. Use `E'...'` for `\n`, `\t`, and other escapes.
- Comments are `-- ...` and `/* ... */`. A block comment does not nest.
- `--` always starts a comment. `10 --5` is `10`. Write `10 - -5`.
- A number may group digits with one `_` between digits: `200_000`. A
  hexadecimal integer such as `0xFF` takes no separators.

```sql
CREATE TABLE `order` (`select` STRING NOT NULL, `from` STRING);
SELECT `select`, `from` FROM `order`;
SELECT 'it''s ready', "say ""hello""", 'C:\Users\data', E'line1\nline2';
SELECT `end` FROM events;
```

### Literals

| Type | Literal |
| --- | --- |
| `INT64` | `42`, `200_000`, `0xFF` |
| `NUMERIC` | `NUMERIC '0.1'` (typed); a bare decimal is a float in expressions |
| `FLOAT64` | `3.14`, `1e10` |
| `STRING` | `'hello'` or `"hello"`; `E'a\tb'` for escapes |
| `BOOL` | `true`, `false` |
| `OID` | `'507f1f77bcf86cd799439011'` (24 hex chars), or `str_id('...')` |
| `UUID` | `'550e8400-e29b-41d4-a716-446655440000'`, or 32 hex digits |
| `DATE` | `'2026-03-15'` |
| `DATETIME` | `'2026-03-15T12:00:00Z'` (UTC, ISO-8601) |
| `BYTES` | `X'DEADBEEF'` or `x'...'`, an even digit count |
| `ARRAY(T)` | `ARRAY[1, 2, 3]`, `ARRAY[]` |

CamusDB parses date and datetime text as UTC. The date functions reject a
timestamp string with no `Z` or offset, such as `'2024-06-15 10:30:00'`.

## Placeholders

A placeholder is `@name`. It can replace any value, including `LIMIT`,
`OFFSET`, and the `AS OF SYSTEM TIME` value. CamusDB has no `$1`, `?`, or
`:name` placeholder.

```sql
SELECT id, name FROM robots WHERE id = @id LIMIT @limit;
SELECT @p::int64;
SELECT * FROM users WHERE id = ANY (@ids);
```

A prepared statement binds by position. `PREPARE` returns the distinct names in
first-occurrence order. A repeated name fills one slot. You can prepare
`SELECT`, `INSERT`, `UPDATE`, `DELETE`, and `SHOW`, but not DDL or user admin.
Prepare through the API or a driver, not with a SQL `PREPARE` statement. On
`CADB0520` `UnknownPreparedStatement`, prepare again and replay one time.
`CADB0521` means a prepared-statement cap was reached; close unused handles.

## SELECT

Write the clauses in this order:

```sql
SELECT [DISTINCT] projections
FROM source [AS OF SYSTEM TIME ...]
WHERE row_filter
GROUP BY keys
HAVING group_filter
ORDER BY sort_keys
LIMIT n OFFSET m;
```

- Name an output column with `AS`. `ORDER BY` and `HAVING` can use that alias.
- `LIMIT` and `OFFSET` take an integer literal or a placeholder.
- Aggregates are `COUNT(*)`, `COUNT(col)`, `SUM`, `AVG`, `MIN`, and `MAX`.
- Every non-aggregate projection must appear in `GROUP BY`.
- `HAVING` needs a `GROUP BY` or an aggregate projection.
- Operators: `=`, `!=`, `<>`, `<`, `>`, `<=`, `>=`, `BETWEEN` (inclusive),
  `AND`, `OR`, `NOT`, `IS NULL`, `IS NOT NULL`, `IN`, `NOT IN`, `EXISTS`,
  `LIKE`, `ILIKE`, and the regex operators `~`, `~*`, `!~`, `!~*`.
- Arithmetic: `+`, `-`, `*`, `/`, `%`. `%` is `mod()`.
- Regex uses the .NET engine. A pattern is unanchored. Use `\p{L}`, not
  `[[:alpha:]]`. A bad pattern fails with `CADB0400`.
- Write a regex in a plain string with single backslashes: `'^\d+$'`. A plain
  string keeps backslashes as typed. Do not double them.

```sql
SELECT role, COUNT(*) AS cnt
FROM app_users
WHERE active = true AND email ILIKE '%@example.com'
GROUP BY role
HAVING cnt > 1
ORDER BY cnt DESC, role
LIMIT 25 OFFSET 50;

SELECT SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) AS paid_total FROM orders;
```

`NOT IN` against a set that holds a `NULL` returns no row for a non-match. Use
`NOT EXISTS` when the inner column is nullable.

### SELECT without FROM

It returns one row. It accepts projections, `LIMIT`, and `OFFSET` only.

```sql
SELECT 1 + 1;
SELECT EXISTS (SELECT 1 FROM accounts WHERE email = @email);
SELECT (SELECT COUNT(*) FROM accounts) AS total;
```

CamusDB rejects `SELECT *`, an aggregate, `WHERE`, `GROUP BY`, `HAVING`, and
`ORDER BY` without a `FROM`. Wrap an aggregate in a scalar subquery.

## Joins and subqueries

- Joins: `[INNER] JOIN ... ON`, `LEFT [OUTER] JOIN ... ON`,
  `RIGHT [OUTER] JOIN ... ON`, `CROSS JOIN`, and the comma form.
- Give every source an alias. Qualify every column.
- Put the join condition in `ON`. A `WHERE` filter on the right side of a
  `LEFT JOIN` removes the padded rows.
- A `RIGHT JOIN` whose left operand is a join is not supported. Put the
  preserved table on the left.
- `IN (SELECT ...)` must return one column and must not be correlated. Use
  `EXISTS` for a correlated test.
- A scalar subquery with zero rows gives `NULL`. More than one row is an error.
- A derived table needs an alias. Alias each computed column inside it.

```sql
SELECT u.email, d.post_count
FROM app_users u
JOIN (SELECT user_id, COUNT(*) AS post_count FROM posts GROUP BY user_id) d
  ON d.user_id = u.id
WHERE EXISTS (SELECT * FROM posts p WHERE p.user_id = u.id AND p.published = true);
```

## Writes

```sql
INSERT INTO robots (id, name, year) VALUES (GEN_ID(), 'C-3PO', 1977), (GEN_ID(), 'K-2SO', DEFAULT);
UPDATE robots SET year = 1982 WHERE name = 'T-800' RETURNING id, year;
DELETE FROM orders WHERE total > 100 LIMIT 10 RETURNING *;
```

- `UPDATE` and `DELETE` require a `WHERE` clause. To touch every row, write a
  predicate that matches every row. To empty a table, use `TRUNCATE`.
- `UPDATE` and `DELETE` accept `LIMIT n` before `RETURNING`.
- A multi-row `INSERT` is atomic.
- `RETURNING` takes `*`, columns, scalar expressions, aliases, and parameters.
  It rejects aggregates and subqueries (`CADB0400`), sequence calls
  (`CADB0547`), and unknown columns (`CADB0404`). It needs `SELECT` on the target.
- A gRPC non-query reply over 4 MiB fails with `CADB0550`. Use `ExecuteQuery`.
- One transaction may write at most `max_mutations_per_transaction` mutations
  (default 20,000). Excess fails with `CADB0506`. Split the work.

### INSERT ... SELECT and CREATE TABLE ... AS SELECT

```sql
INSERT INTO archived_orders (id, customer, total)
SELECT id, customer, total FROM orders WHERE created_at < '2026-01-01';

CREATE TABLE order_totals AS
SELECT customer, SUM(total) AS total_spent FROM orders GROUP BY customer;

CREATE TABLE IF NOT EXISTS orders_empty AS SELECT * FROM orders WITH NO DATA;
```

- Columns match **by position**, never by name. Counts must agree.
- CTAS copies the result shape only: no index, constraint, default, or comment.
- CTAS adds a leading key `id OID NOT NULL DEFAULT gen_id()`. If the query has
  an `id` column, the key becomes `id2`.
- In CTAS, alias every expression, cast a bare `NULL`
  (`CAST(NULL AS INT64) AS x`), and list columns instead of `*` over a join.
- CTAS commits the table before the rows. A failed load can leave an empty table.
- A source with `AS OF SYSTEM TIME` copies historical rows into the present.

### TRUNCATE

```sql
TRUNCATE TABLE orders;
TRUNCATE orders RESTART IDENTITY;
```

One table per statement. No `CASCADE`. Not inside an explicit transaction
(`CADB0538`). It fails on a referenced parent table (`CADB0530`) and on a view
(`CADB0525`). `SHOW ORPHAN TABLES` and `CREATE TABLE t2 RELINK TO '<id>'`
recover the old rows within the retention window.

## Views and materialized views

```sql
CREATE VIEW open_orders AS SELECT id, customer, total FROM orders WHERE status = 'open';
CREATE OR REPLACE VIEW open_orders AS SELECT id, customer, total, note FROM orders WHERE status = 'open';
CREATE MATERIALIZED VIEW customer_totals AS
  SELECT customer, SUM(total) AS total_spent FROM orders GROUP BY customer;
REFRESH MATERIALIZED VIEW customer_totals;
```

- Every output column of a view needs a name. Alias each expression.
- A body may not mix `*` with other columns, use `AS OF SYSTEM TIME`, or carry hints.
- `CREATE OR REPLACE VIEW` may only append columns (`CADB0529`).
- Every view is read-only. DML on a view or a materialized view fails
  (`CADB0525` `ViewNotUpdatable`).
- A view runs with its **owner's** privileges.
- `DROP TABLE` and `DROP COLUMN` fail while a view depends on them
  (`CADB0530`). `DROP VIEW ... CASCADE` exists; `DROP TABLE ... CASCADE` does not.
- A materialized view changes only on `REFRESH`. `WITH NO DATA` makes reads
  fail with `CADB0531` until a refresh. `CONCURRENTLY` fails with `CADB0533`.
- A materialized view gets a generated `id` key. You can index it.

## Databases and inspection

```sql
CREATE DATABASE IF NOT EXISTS app;
RENAME DATABASE app TO app_prod;            -- or ALTER DATABASE app RENAME TO app_prod
DROP DATABASE IF EXISTS app_prod;           -- FORCE deletes it permanently
SHOW DATABASES; SHOW DATABASE; SHOW TABLES; SHOW VIEWS; SHOW MATERIALIZED VIEWS;
SHOW COLUMNS FROM robots; DESCRIBE robots; SHOW INDEXES FROM robots; SHOW CREATE TABLE robots;
COMMENT ON COLUMN users.email IS 'Unique login email address';
```

- Create a database explicitly. An unknown database gives `CADB0010`.
- `USE app;` is a `camus-cli` shell command. A driver, HTTP, or gRPC client
  names the database in the connection string or request.
- `_system` and `information_schema` are reserved database names.
- `SHOW TABLES` lists tables only, not views.
- `SHOW CREATE TABLE` shows comments; `SHOW COLUMNS` does not.
- `COMMENT ON COLUMN` and `COMMENT ON INDEX` need a `table.name` target.
  `IS NULL` removes a comment. The maximum is 65,535 characters (`CADB0511`).

## Expressions

- Cast with `CAST(x AS type)` or `x::type`. `::` binds tighter than operators:
  write `(a + b)::text`.
- `arr[n]` reads element `n`, starting at 1. Out of range gives `NULL`.
- `x op ANY (array_or_subquery)`, `SOME`, and `ALL` work with `=`, `<>`, `<`,
  `<=`, `>`, `>=`. A subquery on the right must not be correlated.
- All non-`NULL` elements of `ARRAY[...]` must share one type.
- A statement deeper than 1,000 parse levels fails with `CADB0413`. Replace
  long `OR` chains with `IN`, and huge `CASE` with a join.

```sql
SELECT (ARRAY['paid', 'shipped', 'delivered'])[1 + i % 3] FROM series;
SELECT * FROM posts WHERE 'news' = ANY (tags);
SELECT '42'::int + 1, CAST('2026-03-15' AS DATE);
```

## Functions

Read [references/functions.md](references/functions.md) for the full catalog.
Key rules:

- Most functions return `NULL` for a `NULL` argument. `coalesce`, `ifnull`,
  `nvl`, `concat`, and `json_valid` handle `NULL` differently.
- Eight functions are volatile: `gen_id()`, `gen_uuid_v4()`, `gen_uuid_v7()`,
  `current_timestamp()`, `now()`, `current_date()`, `unix_timestamp()`, and `random()`.
- Session functions (`current_user()`, `current_database()`, ...) are not
  allowed in a `DEFAULT` or a `CHECK`.
- Times are UTC. `date_add` returns `DATETIME`, even for a `DATE` input.
- CamusDB checks argument count and types at execution, not at parse.

## Not supported

The docs state these limits:

- `FULL [OUTER] JOIN`, `NATURAL JOIN`, `JOIN ... USING (...)`.
- `COUNT(DISTINCT x)`, `SELECT DISTINCT ... GROUP BY`, `SELECT DISTINCT COUNT(*)`.
- `WITH RECURSIVE`, temporary views, updatable views, `security_invoker`.
- Array slices `a[1:2]`, `UPDATE ... SET a[1] = x`, `@>`, `<@`, array text `'{a,b}'`.
- `NUMERIC(p, s)`, `ARRAY(NUMERIC)`, nested arrays.
- `TRUNCATE a, b`, `TRUNCATE ... CASCADE`, `REFRESH ... CONCURRENTLY`.
- `regexp_matches`, `regexp_split_to_table`, POSIX classes such as `[[:alpha:]]`.

The docs describe no `UNION`, `INTERSECT`, `EXCEPT`, `WITH` (CTE), window
function (`OVER`), `ON CONFLICT`, `ON DUPLICATE KEY`, or `UPSERT`. The engine
grammar has none of them either. Rewrite with derived tables, views, or several
statements in a transaction.

## Common mistakes

- `"col"` as an identifier (PostgreSQL). It is a string. Use backticks.
- `'O\'Brien'` (MySQL). Write `'O''Brien'` or `E'O\'Brien'`.
- `$1` or `?` placeholders. Use `@name`.
- `LIMIT 10, 20` (MySQL). Write `LIMIT 20 OFFSET 10`.
- `UPDATE t SET x = 1` with no `WHERE`. CamusDB rejects it.
- `DELETE FROM t` to empty a table. Use `TRUNCATE TABLE t`, outside a transaction.
- `INSERT INTO t2 SELECT ...` that relies on matching names. Order matters.
- `CREATE VIEW v AS SELECT a + 1 FROM t`. Alias the expression.
- `NUMERIC(10, 2)` from PostgreSQL, MySQL, or Spanner DDL. Write `NUMERIC`.
- `now() + interval '1 day'` or `DATE_ADD(d, INTERVAL 1 DAY)`. Write
  `date_add(d, 1, 'day')`.
- Local timestamp text without an offset. Add `Z` or `+00:00`.
- `sha256(x)` expecting bytes (PostgreSQL). It returns hex text. Use `digest(x, 'sha256')`.
- `NULLIF(a, b)`. The function catalog does not include it. Write
  `CASE WHEN a = b THEN NULL ELSE a END`.
- `a || b` for text. The docs do not describe `||`. Write `concat(a, b)`,
  which skips `NULL` arguments.
- A plain `SELECT ... AS OF SYSTEM TIME` inside an explicit transaction. It works
  only in autocommit, or as the source of `INSERT ... SELECT` or CTAS.
- Sending `USE db;` through a driver. Set the database on the connection.

## References

Reference files in this skill:

- [references/statements.md](references/statements.md): read for the exact
  syntax of a statement, and for the prepared-statement limits.
- [references/functions.md](references/functions.md): read before you call any
  scalar function, or format and parse a date with a pattern.
- [references/porting-from-postgres-mysql.md](references/porting-from-postgres-mysql.md):
  read when you translate SQL from PostgreSQL, MySQL, CockroachDB, or Spanner.

Public docs (prefix `https://camusdb.github.io/docs/`):

- Dialect: https://camusdb.github.io/docs/sql, https://camusdb.github.io/docs/sql-expression-syntax,
  https://camusdb.github.io/docs/data-types, https://camusdb.github.io/docs/statement-nesting-limit
- Reads: https://camusdb.github.io/docs/sql-queries, https://camusdb.github.io/docs/joins-and-subqueries,
  https://camusdb.github.io/docs/sql-fromless-select
- Writes: https://camusdb.github.io/docs/sql-writes, https://camusdb.github.io/docs/insert-select-and-ctas,
  https://camusdb.github.io/docs/truncate-table
- Objects: https://camusdb.github.io/docs/views, https://camusdb.github.io/docs/materialized-views,
  https://camusdb.github.io/docs/databases, https://camusdb.github.io/docs/sql-inspection,
  https://camusdb.github.io/docs/comment-on
- Clients and errors: https://camusdb.github.io/docs/prepared-statements,
  https://camusdb.github.io/docs/error-codes
- Functions: https://camusdb.github.io/docs/functions and each `functions-*` page
