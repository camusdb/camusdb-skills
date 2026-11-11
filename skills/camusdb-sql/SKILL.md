---
name: camusdb-sql
description: Writes, ports, and reviews CamusDB SQL (the CamusSQL dialect). Covers SELECT, SELECT without FROM, joins, correlated subqueries, EXISTS, derived tables, UNION, INTERSECT, EXCEPT, window functions (OVER), generate_series, INSERT, UPDATE, DELETE, RETURNING, ON CONFLICT, UPSERT, INSERT ... SELECT, CREATE TABLE ... AS SELECT, TRUNCATE, views, materialized views, SHOW TABLES, COMMENT ON, jsonb operators (->, ->>, @>), @name placeholders, backtick identifiers, CAST and ::, operator precedence, ^ power, LIKE ... ESCAPE, ANY/ALL, CASE, IF, coalesce, Unicode text, and functions such as gen_id, date_add, date_trunc, to_char, substr, strpos, ln, log, exp, regexp_replace, and sha256. Use it when writing queries for CamusDB, porting SQL from PostgreSQL, MySQL, CockroachDB, or Spanner, or fixing errors such as CADB0400, CADB0403, CADB0404, CADB0406, CADB0407, CADB0413, CADB0420, CADB0421, CADB0506, CADB0520, CADB0525, CADB0529, CADB0533, CADB0538, CADB0547, or CADB0557.
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
- `camusdb-csharp-extensions`: inline C# `CREATE FUNCTION` and `CREATE TRIGGER`.

## Workflow

1. Identify the statement family. Read its syntax in
   [references/statements.md](references/statements.md) or another file in "References".
2. Apply the lexical rules below. Check every quote, identifier, and placeholder.
3. Port SQL from another engine? Read
   [references/porting-from-postgres-mysql.md](references/porting-from-postgres-mysql.md)
   and rewrite each construct it lists.
4. Check every function name against
   [references/functions.md](references/functions.md). Never assume that a
   PostgreSQL or MySQL function exists.
5. Check each statement against "Not supported" below. Rewrite, do not hope.
6. Confirm a `WHERE` on `UPDATE` and `DELETE`. Check the 20,000-mutation limit.
7. Verify a non-trivial read with `EXPLAIN`, and a view with `SHOW CREATE VIEW`.

## Lexical rules

- Keywords and function names ignore case. A name keeps its creation case, and a
  later match ignores case: `Robots` and `robots` cannot both exist.
- Quote an identifier with backticks only. Do this for reserved words, type
  names, and function names. `CASE`, `END`, `COMMENT`, `IF`, `OVER`, and
  `RETURNING` are reserved.
- `'...'` and `"..."` are both **string literals**. A double quote never quotes
  an identifier.
- A plain string does not treat a backslash as an escape. Double the active
  quote to escape it. Use `E'...'` for `\n`, `\t`, and other escapes.
- Comments are `-- ...` and `/* ... */`, with no nesting. `10 --5` is `10`: write `10 - -5`.
- A number may group digits with one `_` between digits: `200_000`. A
  hexadecimal integer such as `0xFF` takes no separators.
- An unknown character outside a literal or comment fails with `CADB0406`, with
  its line and column. A non-breaking space is not white space. A raw tab or line
  break inside a literal is refused: write `E'a\tb'`.
- One request holds one statement. Split a script into requests.

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
SELECT * FROM users WHERE id = ANY (@ids);
```

Prepare through a driver, not with SQL. "Prepared statements" in
[references/statements.md](references/statements.md) has the rules and errors.
Inline SQL over `max_statement_bytes` (default 8 MiB) fails with `CADB0557`. Bind
large values as parameters.

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

- Name an output column with `AS`. `ORDER BY` and `HAVING` can use that alias. An
  unaliased expression is named by its zero-based position, such as `0`. A scalar
  subquery takes its inner name.
- `ORDER BY name` fails (`CADB0400`) when two different outputs share the name.
  `LIMIT` and `OFFSET` take a nonnegative integer literal or placeholder.
- `WHERE`, `HAVING`, and `JOIN ON` need a boolean. `WHERE 1` fails with `CADB0400`.
- Aggregates are `COUNT(*)`, `COUNT(col)`, `SUM`, `AVG`, `MIN`, `MAX`, `jsonb_agg`,
  and `jsonb_object_agg`.
- Every non-aggregate projection must appear in `GROUP BY`. `HAVING` needs a
  `GROUP BY` or an aggregate. It reads a group key, a select item, or an aggregate.
- Operators: `=`, `!=`, `<>`, `<`, `>`, `<=`, `>=`, `[NOT] BETWEEN` (inclusive),
  `AND`, `OR`, `NOT`, `IS NULL`, `IS NOT NULL`, `IN`, `NOT IN`, `EXISTS`,
  `[NOT] LIKE`, `[NOT] ILIKE`, and the regex operators `~`, `~*`, `!~`, `!~*`.
- Arithmetic: `+`, `-`, `*`, `/`, `%`, and `^` (power). `a || b` joins text; a `NULL`
  operand gives `NULL`. Bitwise operators are `&`, `|`, `#` (XOR), `~`, `<<`, and `>>`.
- `LIKE` matches the whole value. `%` is any run, `_` is one code point, and a
  backslash escapes. `ESCAPE '#'` picks another escape character.
- An `IN` list item is a literal, a parameter, or a column of the same row.
- `FOR UPDATE [OF t] [NOWAIT | SKIP LOCKED]` locks the returned rows. See
  `camusdb-transactions`.
- Regex uses the .NET engine: `\p{L}`, not `[[:alpha:]]`. Write `'^\d+$'` with single
  backslashes in a plain string. A bad pattern fails with `CADB0400`.
- `NOT IN` against a set with a `NULL` drops every non-match. Prefer `NOT EXISTS`.
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
- `generate_series` goes in `FROM` only. Its arguments are integers. Read
  [references/set-and-window.md](references/set-and-window.md).

### SELECT without FROM

It reads one synthetic row, with optional `WHERE`, `LIMIT`, and `OFFSET`. It refuses
`*`, aggregates, `GROUP BY`, `HAVING`, and `ORDER BY`: write
`SELECT (SELECT COUNT(*) FROM accounts) AS total;`. It also works as a subquery, a
derived table, or a view body: `SELECT 'seed' WHERE NOT EXISTS (SELECT 1 FROM t);`.

## Joins and subqueries

- Joins: `[INNER] JOIN ... ON`, `LEFT [OUTER] JOIN ... ON`,
  `RIGHT [OUTER] JOIN ... ON`, `CROSS JOIN`, and the comma form.
- Give every source an alias. Qualify every column.
- Put the join condition in `ON`. A `WHERE` filter on the right side of a
  `LEFT JOIN` removes the padded rows.
- A `RIGHT JOIN` whose left operand is a join fails with `CADB0533`. Put the
  preserved table on the left.
- `IN (SELECT ...)` must return one column.
- An uncorrelated subquery works in any expression position: the select list,
  `HAVING`, `JOIN ON`, `UPDATE ... SET`, and `INSERT ... VALUES`. `ORDER BY` and
  `GROUP BY` refuse one with `CADB0400`.
- Correlated scalar, `EXISTS`, `IN`/`NOT IN`, and `ANY`/`ALL` subqueries work in
  `SELECT ... WHERE`, a select list without grouping, aggregates, or windows,
  `UPDATE ... SET`/`WHERE`, and `DELETE ... WHERE`. Elsewhere they fail with
  `CADB0533`. A name resolves in the nearest query first. Qualify columns.
- A scalar subquery with zero rows gives `NULL`. More than one row is an error.
  Read "Subqueries" in [references/statements.md](references/statements.md).
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
  `RETURNING`. To empty a table, use `TRUNCATE [TABLE] t [RESTART IDENTITY]`: one
  table, no `CASCADE`, and not in an explicit transaction (`CADB0538`).
- A multi-row `INSERT` is atomic. A repeated target column fails with `CADB0403`.
- `ON CONFLICT` takes a column list or `ON CONSTRAINT name`, then `DO NOTHING` or
  `DO UPDATE`. `DO UPDATE` must name a target. `excluded.c` is the proposed row.
  `UPSERT` updates on the primary key. Read
  [references/statements.md](references/statements.md) for both forms.
- Any failed statement ends an explicit transaction. There are no savepoints.
- `RETURNING` takes `*`, columns, scalar expressions, aliases, and parameters. It
  refuses aggregates and subqueries (`CADB0400`), sequence calls (`CADB0547`), and
  unknown columns (`CADB0404`). It needs `SELECT`. `UPDATE` returns the new row.
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
- The source can read a plain view. A scalar subquery column keeps its type when empty.
- A source with `AS OF SYSTEM TIME` copies historical rows into the present. Use it
  to copy from a busy table. See [references/statements.md](references/statements.md).

## Views and materialized views

```sql
CREATE VIEW open_orders AS SELECT id, customer, total FROM orders WHERE status = 'open';
CREATE MATERIALIZED VIEW customer_totals AS
  SELECT customer, SUM(total) AS total_spent FROM orders GROUP BY customer;
REFRESH MATERIALIZED VIEW customer_totals;
```

- Alias each expression of a view. A body may not mix `*` with other columns, use
  `AS OF SYSTEM TIME`, or carry hints. It can be a set operation.
- `CREATE VIEW` binds the body without running it. A runtime error appears on read.
- `CREATE OR REPLACE VIEW` may only append columns (`CADB0529`).
- Every view is read-only (`CADB0525`). A view runs with its **owner's** privileges.
- A materialized view changes only on `REFRESH` (no `CONCURRENTLY`). `DROP TABLE`
  fails while a view depends on it (`CADB0530`).

## Databases and inspection

- Create a database explicitly: `CREATE DATABASE IF NOT EXISTS app;`. An unknown
  database gives `CADB0010`. `USE app;` is a `camus-cli` command only.
- `SHOW TABLES` lists tables only. Use `SHOW VIEWS` and `SHOW MATERIALIZED VIEWS`.
- `DROP INDEX idx ON t` needs the table. The full syntax is in
  [references/statements.md](references/statements.md).

## Expressions

- Cast with `CAST(x AS type)` or `x::type`. `::` binds tighter than operators:
  write `(a + b)::text`.
- A float cast to `INT64` rounds half away from zero: `2.5` gives `3`. A string must
  spell an integer. `::int` and `::smallint` check 32-bit and 16-bit ranges (`CADB0420`).
- Two values compare only when their types have a rule. `5 = '5'` and `bool_col = 1`
  fail with `CADB0400`. Cast one side. `date` with `datetime`, and a string with a
  date, work. Read "Comparison rule" in [references/functions.md](references/functions.md).
- `CASE`, `COALESCE`, `IFNULL`, `NVL`, and `IF(cond, a, b)` return one type. Numbers
  widen. `COALESCE(1, 'a')` fails with `CADB0400`, also in a branch that never runs.
  `COALESCE`, `IFNULL`, and `NVL` stop at the first non-`NULL` value.
- A known mistake fails before any row is read, even on an empty table: an unknown
  function, a wrong argument count, a type clash, or a constant `1 / 0`.
- Precedence follows PostgreSQL. `^` binds above `*`. `||`, bitwise, regex, and
  `jsonb` operators share one level. `IS` binds below comparisons, and `a = b = c`
  fails. Read [references/expressions.md](references/expressions.md).
- `INT64` arithmetic never wraps: past the range it fails with `CADB0420`. A float
  overflow or underflow fails with `CADB0421`. `'Infinity'` and `'NaN'` are values.
- `arr[n]` reads element `n`, starting at 1. Out of range gives `NULL`. All non-`NULL`
  elements of `ARRAY[...]` must share one type.
- `x op ANY (array_or_subquery)`, `SOME`, and `ALL` take any comparison operator.
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
- Text positions and lengths count code points. Sorting uses binary UTF-16 order,
  with no `COLLATE`.

## Not supported

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
- `'O\'Brien'` or `LIMIT 10, 20` (MySQL). Write `'O''Brien'` and `LIMIT 20 OFFSET 10`.
- `now() + interval '1 day'`. Write `date_add(now(), 1, 'day')`.
- `a || b` when an operand can be `NULL`. The result is `NULL`. Write `concat(a, b)`,
  which skips `NULL` arguments. `||` is not a logical OR.
- `WHERE int_col = '5'` (MySQL). It fails with `CADB0400`. Write `int_col = 5`.
- `'k=' || doc ->> 'k'`. It reads `('k=' || doc) ->> 'k'`. Write `'k=' || (doc ->> 'k')`.
- A `LIKE` pattern with a literal `_` or `\`. Escape it: `'a\_%'`.
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
- [references/expressions.md](references/expressions.md): operator precedence, `LIKE`,
  checks before rows are read, output names, `LIMIT` rules, and Unicode text.
- [references/porting-from-postgres-mysql.md](references/porting-from-postgres-mysql.md):
  read when you translate SQL from PostgreSQL, MySQL, CockroachDB, or Spanner.

Public docs, under `https://camusdb.github.io/docs/` (append the page name):

- Dialect: `sql`, `sql-expression-syntax`, `data-types`, `json`, `statement-nesting-limit`.
- Reads: `sql-queries` (`#set-operations`, `#window-functions`, `#for-update`),
  `joins-and-subqueries`, `sql-fromless-select`.
- Writes: `sql-writes` (`#on-conflict`, `#returning`), `insert-select-and-ctas`, `truncate-table`.
- Objects: `views`, `materialized-views`, `databases`, `sql-inspection`, `comment-on`.
- Other: `prepared-statements`, `error-codes`, `settings-reference`, `functions`, `functions-*`.
