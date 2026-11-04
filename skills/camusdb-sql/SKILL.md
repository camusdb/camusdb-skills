---
name: camusdb-sql
description: Writes, ports, and reviews CamusDB SQL (the CamusSQL dialect). Covers SELECT, joins, subqueries, derived tables, UNION, INTERSECT, EXCEPT, window functions (OVER, row_number), generate_series, INSERT, UPDATE, DELETE, RETURNING, ON CONFLICT, UPSERT, INSERT ... SELECT, CREATE TABLE ... AS SELECT, TRUNCATE, views, materialized views, databases, SHOW TABLES, DESCRIBE, COMMENT ON, jsonb operators and functions (->, ->>, @>, jsonb_set), @name placeholders, prepared statements, backtick identifiers, CAST and ::, ANY/ALL, CASE, IF, NULLIF, coalesce, and scalar functions such as gen_id, date_add, date_trunc, EXTRACT, make_date, to_char, json_value, regexp_replace, and sha256. Use it when writing queries for CamusDB, porting SQL from PostgreSQL, MySQL, CockroachDB, or Spanner, or fixing errors such as CADB0400, CADB0403, CADB0404, CADB0406, CADB0413, CADB0418, CADB0420, CADB0421, CADB0506, CADB0520, CADB0525, CADB0529, CADB0531, CADB0538, CADB0550, or CADB0557.
---

# CamusDB SQL

CamusDB speaks a compact SQL dialect. Most of it looks like PostgreSQL, with
some MySQL and Spanner forms. Many common PostgreSQL and MySQL features are
missing, and some familiar syntax means something else. Use this skill to emit
SQL that CamusDB accepts, and to port SQL from another engine.

Other skills cover nearby topics. Point to them instead of going deep:

- `camusdb-schema-design`: `CREATE TABLE`, types, keys, indexes, `jsonb` columns,
  GIN indexes, constraints, sequences.
- `camusdb-transactions`: `BEGIN`, isolation, locks, `SELECT ... FOR UPDATE`, retries,
  transaction limits.
- `camusdb-query-performance`: `EXPLAIN`, plans, statistics, hints, the result cache.

## Workflow

1. Identify the statement family. Read
   [references/statements.md](references/statements.md) for its exact syntax. See
   "References" below for set operations, window functions, and `jsonb`.
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
  names, and function names. `CASE`, `END`, `COMMENT`, `IF`, `OVER`, and
  `RETURNING` are reserved.
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
```

Read "Literals" in [references/statements.md](references/statements.md) for each type.
`NUMERIC '0.1'` is exact, but a bare `0.1` is a float. `X'DEADBEEF'` is bytes, but
`0xFF` is an integer. CamusDB parses date text as UTC. The date functions reject a
timestamp string with no `Z` or offset, such as `'2024-06-15 10:30:00'`.

## Placeholders

A placeholder is `@name`. It can replace any value, including `LIMIT`,
`OFFSET`, and the `AS OF SYSTEM TIME` value. CamusDB has no `$1`, `?`, or
`:name` placeholder. `?` is a `jsonb` operator.

```sql
SELECT id, name FROM robots WHERE id = @id LIMIT @limit;
SELECT @p::int64;
SELECT * FROM users WHERE id = ANY (@ids);
```

A prepared statement binds by position, in first-occurrence order. A repeated name
fills one slot. You can prepare `SELECT`, `INSERT`, `UPDATE`, `DELETE`, and `SHOW`,
but not DDL or user admin. Prepare through the API or a driver, not with SQL. On
`CADB0520`, prepare again and replay one time. On `CADB0521`, close unused handles.
Inline SQL over `max_statement_bytes` (default 8 MiB) fails with `CADB0557` before the
parse. Bind large values as parameters.

## SELECT

Write the clauses in this order:

```sql
SELECT [DISTINCT] projections
FROM source [AS OF SYSTEM TIME ...]
WHERE row_filter
GROUP BY keys
HAVING group_filter
ORDER BY sort_keys
LIMIT n OFFSET m
FOR UPDATE;
```

- Name an output column with `AS`. `ORDER BY` and `HAVING` can use that alias.
- `LIMIT` and `OFFSET` take an integer literal or a placeholder.
- Aggregates are `COUNT(*)`, `COUNT(col)`, `SUM`, `AVG`, `MIN`, `MAX`, `jsonb_agg`,
  and `jsonb_object_agg`.
- Every non-aggregate projection must appear in `GROUP BY`.
- `HAVING` needs a `GROUP BY` or an aggregate projection. It reads a group key, a
  select item, or an aggregate.
- Operators: `=`, `!=`, `<>`, `<`, `>`, `<=`, `>=`, `[NOT] BETWEEN` (inclusive),
  `AND`, `OR`, `NOT`, `IS NULL`, `IS NOT NULL`, `IN`, `NOT IN`, `EXISTS`,
  `[NOT] LIKE`, `[NOT] ILIKE`, and the regex operators `~`, `~*`, `!~`, `!~*`.
- Arithmetic: `+`, `-`, `*`, `/`, `%`. `a || b` joins text; a `NULL` operand gives
  `NULL`. Bitwise operators are `&`, `|`, `#` (XOR), `~`, `<<`, and `>>`.
- An `IN` list item is a literal, a parameter, or a column name of the same row. An
  expression item is a syntax error.
- `FOR UPDATE [OF t] [NOWAIT | SKIP LOCKED]` locks the returned rows. See
  `camusdb-transactions`.
- Regex uses the .NET engine. Use `\p{L}`, not `[[:alpha:]]`. A bad pattern fails with
  `CADB0400`. Write `'^\d+$'` with single backslashes in a plain string.
- `NOT IN` against a set that holds a `NULL` returns no row for a non-match. Use
  `NOT EXISTS` when the inner column is nullable.
- `NULL` sorts first ascending and last descending. `NULLS FIRST` is not supported.

```sql
SELECT role, COUNT(*) AS cnt FROM app_users
WHERE active = true AND email ILIKE '%@example.com'
GROUP BY role HAVING cnt > 1 ORDER BY cnt DESC, role LIMIT 25 OFFSET 50;
```

### Set operations, window functions, and series

```sql
SELECT id FROM a UNION ALL SELECT id FROM b ORDER BY 1 LIMIT 10;

SELECT * FROM (
  SELECT customer, id, row_number() OVER (PARTITION BY customer ORDER BY total DESC, id) AS rn
  FROM orders
) ranked WHERE rn <= 3;

INSERT INTO scores (id, score) SELECT i, i * 10 FROM generate_series(1, 1000) AS i;
```

- Set operations match columns by position. A combined `ORDER BY` takes an output
  name or a position, not an expression.
- A window call goes in the select list or the final `ORDER BY` only.
- `generate_series` goes in `FROM` only. Its arguments are integers.

Read [references/set-and-window.md](references/set-and-window.md) for the rules.

### SELECT without FROM

It returns one row. It accepts projections, `LIMIT`, and `OFFSET` only. It rejects
`SELECT *`, an aggregate, `WHERE`, `GROUP BY`, `HAVING`, and `ORDER BY`. Wrap an
aggregate in a scalar subquery: `SELECT (SELECT COUNT(*) FROM accounts) AS total;`.

## Joins and subqueries

- Joins: `[INNER] JOIN ... ON`, `LEFT [OUTER] JOIN ... ON`,
  `RIGHT [OUTER] JOIN ... ON`, `CROSS JOIN`, and the comma form.
- Give every source an alias. Qualify every column.
- Put the join condition in `ON`. A `WHERE` filter on the right side of a
  `LEFT JOIN` removes the padded rows.
- A `RIGHT JOIN` whose left operand is a join fails with `CADB0533`. Put the
  preserved table on the left.
- `IN (SELECT ...)` must return one column and must not be correlated.
- An uncorrelated subquery works in any expression position: the select list,
  `HAVING`, `JOIN ON`, `UPDATE ... SET`, and `INSERT ... VALUES`. `ORDER BY` and
  `GROUP BY` refuse one with `CADB0400`.
- A correlated `EXISTS` works only in the `WHERE` of a `SELECT`. Elsewhere it fails
  with `CADB0533`. A correlated scalar subquery is refused.
- A scalar subquery with zero rows gives `NULL`. More than one row is an error.
- A derived table needs an alias. Alias each computed column inside it.

```sql
SELECT u.email, d.post_count FROM app_users u
JOIN (SELECT user_id, COUNT(*) AS post_count FROM posts GROUP BY user_id) d ON d.user_id = u.id
WHERE EXISTS (SELECT * FROM posts p WHERE p.user_id = u.id AND p.published = true);
```

## Writes

```sql
INSERT INTO robots (id, name, year) VALUES (GEN_ID(), 'C-3PO', 1977), (GEN_ID(), 'K-2SO', DEFAULT);
INSERT INTO counters (k, n) VALUES ('hits', 1)
  ON CONFLICT (k) DO UPDATE SET n = counters.n + excluded.n RETURNING k, n;
UPSERT INTO counters (k, n) VALUES ('hits', 1);
UPDATE robots SET year = 1982 WHERE name = 'T-800' RETURNING id, year;
DELETE FROM orders WHERE total > 100 LIMIT 10 RETURNING *;
```

- `UPDATE` and `DELETE` require a `WHERE` clause. They accept `LIMIT n` before
  `RETURNING`. To empty a table, use `TRUNCATE`.
- A multi-row `INSERT` is atomic. A repeated target column fails with `CADB0403`.
- `ON CONFLICT` takes a column list or `ON CONSTRAINT name`, then `DO NOTHING` or
  `DO UPDATE`. `DO UPDATE` must name a target. `excluded.c` is the proposed row.
  `UPSERT` updates on the primary key. Read
  [references/statements.md](references/statements.md) for both forms.
- Any failed statement ends an explicit transaction. There are no savepoints.
- `RETURNING` takes `*`, columns, scalar expressions, aliases, and parameters.
  It rejects aggregates and subqueries (`CADB0400`), sequence calls
  (`CADB0547`), and unknown columns (`CADB0404`). It needs `SELECT` on the target.
  `UPDATE` returns the new row only.
- A gRPC non-query reply over 4 MiB fails with `CADB0550`. Use `ExecuteQuery`.
- One transaction may write at most `max_mutations_per_transaction` mutations
  (default 20,000). Excess fails with `CADB0506`. Split the work.

### INSERT ... SELECT and CREATE TABLE ... AS SELECT

```sql
INSERT INTO archived_orders (id, customer, total)
SELECT id, customer, total FROM orders WHERE created_at < '2026-01-01';

CREATE TABLE order_totals AS
SELECT customer, SUM(total) AS total_spent FROM orders GROUP BY customer;
```

- Columns match **by position**, never by name. Counts must agree.
- CTAS copies the result shape only, and adds a leading key `id OID`. Alias every
  expression. Cast a bare `NULL`. List columns instead of `*` over a join.
- A source with `AS OF SYSTEM TIME` copies historical rows into the present. Use it
  to copy from a busy table. See [references/statements.md](references/statements.md).

### TRUNCATE

`TRUNCATE [TABLE] t [RESTART IDENTITY]` takes one table and no `CASCADE`. It fails
inside an explicit transaction (`CADB0538`), on a referenced parent table
(`CADB0530`), and on a view (`CADB0525`).

## Views and materialized views

```sql
CREATE VIEW open_orders AS SELECT id, customer, total FROM orders WHERE status = 'open';
CREATE MATERIALIZED VIEW customer_totals AS
  SELECT customer, SUM(total) AS total_spent FROM orders GROUP BY customer;
REFRESH MATERIALIZED VIEW customer_totals;
```

- Alias each expression of a view. A body may not mix `*` with other columns, use
  `AS OF SYSTEM TIME`, or carry hints. It can be a set operation.
- `CREATE OR REPLACE VIEW` may only append columns (`CADB0529`).
- Every view is read-only (`CADB0525`). A view runs with its **owner's** privileges.
- A materialized view changes only on `REFRESH`. `CONCURRENTLY` fails with `CADB0533`.
- `DROP TABLE` fails while a view depends on it (`CADB0530`). It has no `CASCADE`.

## Databases and inspection

```sql
CREATE DATABASE IF NOT EXISTS app;
DROP DATABASE IF EXISTS app;                -- FORCE deletes it permanently
SHOW DATABASES; SHOW TABLES; SHOW VIEWS; SHOW MATERIALIZED VIEWS;
SHOW TABLES FROM app LIKE 'order%';         -- FROM or IN; needs no current database
SHOW COLUMNS FROM robots; DESCRIBE robots; SHOW INDEXES FROM robots; SHOW CREATE TABLE robots;
COMMENT ON COLUMN users.email IS 'Unique login email address';
DROP INDEX year_idx ON robots;              -- the table is required
```

- Create a database explicitly. An unknown database gives `CADB0010`.
- `USE app;` is a `camus-cli` shell command. A driver, HTTP, or gRPC client
  names the database in the connection string or request.
- `SHOW TABLES` lists tables only, not views.

## Expressions

- Cast with `CAST(x AS type)` or `x::type`. `::` binds tighter than operators:
  write `(a + b)::text`.
- A float cast to `INT64` rounds half away from zero: `2.5` gives `3`. A string must
  spell an integer. `CAST('2.5' AS int64)` fails with `CADB0400`.
- Two values compare only when their types have a rule. `5 = '5'` and `bool_col = 1`
  fail with `CADB0400`. Cast one side. `date` with `datetime`, and a string with a
  date, work. Read "Comparison rule" in [references/functions.md](references/functions.md).
- `CASE`, `COALESCE`, `IFNULL`, `NVL`, and `IF(cond, a, b)` return one type. Numbers
  widen. `COALESCE(1, 'a')` fails with `CADB0400`, also in a branch that never runs.
- `INT64` arithmetic never wraps: past the range it fails with `CADB0420`. A float
  overflow or underflow fails with `CADB0421`. `'Infinity'` and `'NaN'` are values.
- `arr[n]` reads element `n`, starting at 1. Out of range gives `NULL`. All non-`NULL`
  elements of `ARRAY[...]` must share one type.
- `x op ANY (array_or_subquery)`, `SOME`, and `ALL` work with `=`, `<>`, `<`,
  `<=`, `>`, `>=`. A subquery on the right must not be correlated.
- A statement deeper than 1,000 parse levels fails with `CADB0413`. So does one with
  more than 64 nested set operations. Replace a huge `CASE` with a join.

```sql
SELECT * FROM posts WHERE 'news' = ANY (tags);
SELECT '42'::int + 1, CAST('2026-03-15' AS DATE), (ARRAY['a', 'b'])[1 + i % 2] FROM series;
```

## Functions

Read [references/functions.md](references/functions.md) for the full catalog.
Key rules:

- Most functions return `NULL` for a `NULL` argument. `coalesce`, `ifnull`,
  `nvl`, `nullif`, `if`, `concat`, and `json_valid` handle `NULL` differently.
- `gen_id()`, `gen_uuid_v4()`, `gen_uuid_v7()`, `random()`, and `clock_timestamp()`
  change per call. `now()`, `current_timestamp()`, `current_date()`, and
  `unix_timestamp()` report the start of the transaction. Write the parentheses.
- Session functions (`current_user()`, `current_database()`, ...) are not
  allowed in a `DEFAULT` or a `CHECK`.
- Times are UTC. `date_add` returns `DATETIME`, even for a `DATE` input.

## Not supported

The docs state these limits:

- `FULL [OUTER] JOIN`, `NATURAL JOIN`, `JOIN ... USING (...)`, `LATERAL`.
- `COUNT(DISTINCT x)`, `SELECT DISTINCT ... GROUP BY`, `SELECT DISTINCT COUNT(*)`.
- `WITH` (CTE), `WITH RECURSIVE`, `CORRESPONDING BY`, temporary views, updatable
  views, `security_invoker`. Use a derived table or a view for a CTE.
- `FILTER (WHERE ...)`, the `WINDOW` clause, `GROUPS` frames, `EXCLUDE`, `NULLS FIRST`,
  `ON DUPLICATE KEY UPDATE`, `MERGE`, `FOR SHARE`, `unnest`, `WITH ORDINALITY`.
- Array slices `a[1:2]`, `UPDATE ... SET a[1] = x`, `@>` and `<@` on an array, array
  text `'{a,b}'`. `@>`, `<@`, and `'{a,b}'` paths work on `jsonb`.
- `jsonpath`, `doc['key']`, `row_to_json`, `jsonb_agg(x ORDER BY y)`, `NUMERIC(p, s)`,
  `ARRAY(NUMERIC)`, nested arrays.
- `TRUNCATE a, b`, `TRUNCATE ... CASCADE`, `REFRESH ... CONCURRENTLY`.
- `regexp_matches`, `regexp_split_to_table`, POSIX classes such as `[[:alpha:]]`.

## Common mistakes

- `"col"` as an identifier (PostgreSQL). It is a string. Use backticks.
- `'O\'Brien'` (MySQL). Write `'O''Brien'` or `E'O\'Brien'`.
- `LIMIT 10, 20` (MySQL). Write `LIMIT 20 OFFSET 10`.
- `now() + interval '1 day'` or `DATE_ADD(d, INTERVAL 1 DAY)`. Write
  `date_add(d, 1, 'day')`.
- Local timestamp text without an offset. Add `Z` or `+00:00`.
- `sha256(x)` expecting bytes (PostgreSQL). It returns hex text. Use `digest(x, 'sha256')`.
- `a || b` when an operand can be `NULL`. The result is `NULL`. Write `concat(a, b)`,
  which skips `NULL` arguments. `||` is not a logical OR.
- `WHERE int_col = '5'` (MySQL). It fails with `CADB0400`. Write `int_col = 5`.
- A plain `SELECT ... AS OF SYSTEM TIME` inside an explicit transaction. It works
  only in autocommit, or as the source of `INSERT ... SELECT` or CTAS.

## References

Read these files in this skill:

- [references/statements.md](references/statements.md): exact statement syntax,
  `ON CONFLICT`, `RETURNING`, prepared-statement limits, and error codes.
- [references/functions.md](references/functions.md): read before you call any
  scalar function, or format and parse a date with a pattern.
- [references/set-and-window.md](references/set-and-window.md): `UNION`, `INTERSECT`,
  `EXCEPT`, `OVER`, and `generate_series`.
- [references/jsonb.md](references/jsonb.md): `jsonb` operators, functions, and aggregates.
- [references/porting-from-postgres-mysql.md](references/porting-from-postgres-mysql.md):
  read when you translate SQL from PostgreSQL, MySQL, CockroachDB, or Spanner.

Public docs, under `https://camusdb.github.io/docs/` (append the page name):

- Dialect: `sql`, `sql-expression-syntax`, `data-types`, `json`, `statement-nesting-limit`.
- Reads: `sql-queries` (`#set-operations`, `#window-functions`, `#for-update`),
  `joins-and-subqueries`, `sql-fromless-select`.
- Writes: `sql-writes` (`#on-conflict`, `#returning`), `insert-select-and-ctas`, `truncate-table`.
- Objects: `views`, `materialized-views`, `databases`, `sql-inspection`, `comment-on`.
- Other: `prepared-statements`, `error-codes`, `settings-reference`, `functions`, `functions-*`.
