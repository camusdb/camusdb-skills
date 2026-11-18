---
name: camusdb-sql
description: Writes, ports, and reviews CamusDB SQL (the CamusSQL dialect). Covers SELECT, SELECT without FROM, joins, LATERAL, VALUES lists, correlated subqueries, EXISTS, derived tables, WITH (CTE), WITH RECURSIVE, UNION, INTERSECT, EXCEPT, window functions (OVER), generate_series, INSERT, UPDATE ... FROM, DELETE ... USING, RETURNING, ON CONFLICT, UPSERT, INSERT ... SELECT, CREATE TABLE ... AS SELECT, TRUNCATE, views, materialized views, SHOW TABLES, jsonb operators (->, ->>, @>), @name placeholders, backtick identifiers, CAST and ::, operator precedence, LIKE ... ESCAPE, ANY/ALL, CASE, coalesce, INTERVAL arithmetic, DATE and TIMESTAMP literals, and functions such as gen_id, date_add, date_trunc, date_bin, age, to_char, substr, regexp_replace, and sind. Use it when writing queries for CamusDB, porting SQL from PostgreSQL, MySQL, CockroachDB, or Spanner, or fixing errors such as CADB0400, CADB0403, CADB0404, CADB0406, CADB0407, CADB0413, CADB0420, CADB0506, CADB0525, CADB0533, CADB0538, CADB0547, CADB0557, or CADB0560.
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
3. Port SQL from another engine? Rewrite each construct listed in
   [references/porting-from-postgres-mysql.md](references/porting-from-postgres-mysql.md).
4. Check every function name in [references/functions.md](references/functions.md).
   Never assume that a PostgreSQL or MySQL function exists.
5. Check each statement against "Not supported" below. Rewrite, do not hope.
6. Confirm a `WHERE` on `UPDATE` and `DELETE`. Check the 20,000-mutation limit.
7. Verify a non-trivial read with `EXPLAIN`, and a view with `SHOW CREATE VIEW`.

## Lexical rules

- Keywords and function names ignore case. A name keeps its creation case, and a
  later match ignores case: `Robots` and `robots` cannot both exist.
- Quote an identifier with backticks only. Do this for reserved words, type
  names, and function names. `CASE`, `END`, `COMMENT`, `IF`, `OVER`, `LATERAL`,
  `USING`, and `RETURNING` are reserved.
- `'...'` and `"..."` are both **string literals**. A double quote never quotes
  an identifier.
- A plain string does not treat a backslash as an escape. Double the active
  quote to escape it. Use `E'...'` for `\n`, `\t`, and other escapes.
- Comments are `-- ...` and `/* ... */`, with no nesting. `10 --5` is `10`: write `10 - -5`.
- A number may group digits with one `_`: `200_000`. A hex integer (`0xFF`) may not.
- An unknown character outside a literal or comment fails with `CADB0406`, with
  its line and column. A non-breaking space is not white space. A raw tab or line
  break inside a literal is refused: write `E'a\tb'`.
- One request holds one statement. Split a script into requests.

```sql
CREATE TABLE `order` (`select` STRING NOT NULL, `from` STRING);
SELECT `select`, `from` FROM `order`;
SELECT 'it''s ready', "say ""hello""", 'C:\Users\data', E'line1\nline2';
```

Read "Literals" in [references/statements.md](references/statements.md). `NUMERIC '0.1'`
is exact, but a bare `0.1` is a float. `X'DEADBEEF'` is bytes, but `0xFF` is an integer.
`DATE '2026-03-15'` and `TIMESTAMP '2026-03-15T12:00:00Z'` are typed; a space before the
time fails. Date text is UTC. Date functions reject a timestamp with no `Z` or offset.

## Placeholders

A placeholder is `@name`: `WHERE id = @id LIMIT @limit`, `WHERE id = ANY (@ids)`. It
can replace any value, including `LIMIT`, `OFFSET`, and the `AS OF SYSTEM TIME`
value. CamusDB has no `$1`, `?`, or `:name` placeholder. `?` is a `jsonb` operator.

Prepare through a driver, not with SQL. "Prepared statements" in
[references/statements.md](references/statements.md) has the rules and errors.
Inline SQL over `max_statement_bytes` (default 8 MiB) fails with `CADB0557`. Bind
large values as parameters.

## SELECT

```sql
-- Clause order
[WITH name AS (query) [, ...]]
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
- `WHERE`, `HAVING`, and `JOIN ON` need a boolean. `WHERE 1` fails with `CADB0400`.
- Aggregates are `COUNT(*)`, `COUNT(col)`, `SUM`, `AVG`, `MIN`, `MAX`, `jsonb_agg`,
  and `jsonb_object_agg`.
- Every non-aggregate projection must appear in `GROUP BY`. `HAVING` needs a
  `GROUP BY` or an aggregate. It reads a group key, a select item, or an aggregate.
- Operators: comparisons, `[NOT] BETWEEN` (inclusive), `AND`, `OR`, `NOT`,
  `IS [NOT] NULL`, `[NOT] IN`, `EXISTS`, `[NOT] [I]LIKE`, regex `~`, `~*`, `!~`, `!~*`.
- Arithmetic: `+`, `-`, `*`, `/`, `%`, and `^` (power). `a || b` joins text; a `NULL`
  operand gives `NULL`. Bitwise operators are `&`, `|`, `#` (XOR), `~`, `<<`, and `>>`.
- `LIKE` matches the whole value. `%` is any run, `_` is one code point, and a
  backslash escapes. `ESCAPE '#'` picks another escape character.
- An `IN` list item is a literal, a parameter, or a column of the same row.
- `FOR UPDATE [OF t] [NOWAIT | SKIP LOCKED]` locks the returned rows. It cannot read
  a CTE. See `camusdb-transactions`.
- Regex uses the .NET engine: `\p{L}`, not `[[:alpha:]]`. Write `'^\d+$'` with single
  backslashes in a plain string. A bad pattern fails with `CADB0400`.
- `NOT IN` against a set with a `NULL` drops every non-match. Prefer `NOT EXISTS`.
- `NULL` sorts first ascending and last descending. `NULLS FIRST` is not supported.

### WITH queries (CTEs)

```sql
WITH totals AS (SELECT customer_id, SUM(total) AS spent FROM orders GROUP BY customer_id)
SELECT customer_id, spent FROM totals WHERE spent > 1000 ORDER BY spent DESC;
WITH RECURSIVE tree (id, depth) AS (
  SELECT id, 0 FROM categories WHERE parent_id IS NULL
  UNION ALL
  SELECT c.id, t.depth + 1 FROM tree AS t JOIN categories AS c ON c.parent_id = t.id
)
SELECT id, depth FROM tree ORDER BY depth, id;
```

- `WITH` can precede `SELECT`, `INSERT`, `UPSERT`, `UPDATE`, and `DELETE`, and works
  in CTAS, views, subqueries, derived tables, and set-operation operands.
- A CTE read once is inlined; one read twice or more is computed once. Write
  `MATERIALIZED` or `NOT MATERIALIZED` to choose. A write inside a CTE is refused.
- A recursive CTE is `seed UNION [ALL] step`. Add a stop condition. Past 10,000 steps
  or 10,000,000 rows (the defaults), it fails with `CADB0560`. Read
  [references/ctes-and-lateral.md](references/ctes-and-lateral.md).

### Set operations, window functions, and series

```sql
SELECT id FROM a UNION ALL SELECT id FROM b ORDER BY 1 LIMIT 10;
SELECT * FROM (SELECT id, row_number() OVER (PARTITION BY customer ORDER BY total DESC, id) AS rn
               FROM orders) ranked WHERE rn <= 3;
INSERT INTO scores (id, score) SELECT i, i * 10 FROM generate_series(1, 1000) AS i;
```

- Set operations match columns by position. A combined `ORDER BY` takes an output
  name or a position, not an expression.
- A window call goes in the select list or the final `ORDER BY` only.
- `generate_series` goes in `FROM` only. Its arguments are integers. Read
  [references/set-and-window.md](references/set-and-window.md).
- `SELECT` without `FROM` reads one row, with optional `WHERE`, `LIMIT`, and `OFFSET`.
  It refuses `*`, aggregates, `GROUP BY`, `HAVING`, and `ORDER BY`: write
  `SELECT (SELECT COUNT(*) FROM accounts) AS total;`.

## Joins and subqueries

- Joins: `[INNER] JOIN ... ON`, `LEFT [OUTER] JOIN ... ON`, `RIGHT [OUTER] JOIN ... ON`,
  `CROSS JOIN`, and the comma form. Give every source an alias. Qualify every column.
- Put the join condition in `ON`. A `WHERE` filter on the right side of a
  `LEFT JOIN` removes the padded rows.
- A `RIGHT JOIN` whose left operand is a join fails with `CADB0533`. Put the
  preserved table on the left.
- `CROSS JOIN LATERAL (...) AS o` and `LEFT JOIN LATERAL (...) AS o ON true` read
  columns of sources to the left. A table-function argument does so without the
  keyword: `FROM tasks t, generate_series(1, t.n) AS n`.
- `(VALUES (1, 'a'), (2, 'b')) AS v (id, label)` is an inline relation. The alias
  is required.
- `IN (SELECT ...)` must return one column. `SELECT *` works when it expands to one.
- An uncorrelated subquery works in any expression position. `ORDER BY` and
  `GROUP BY` refuse one with `CADB0400`.
- Correlated scalar, `EXISTS`, `IN`/`NOT IN`, and `ANY`/`ALL` subqueries work in
  `SELECT ... WHERE`, a select list without grouping, aggregates, or windows,
  `UPDATE ... SET`/`WHERE`, and `DELETE ... WHERE`. Elsewhere they fail with
  `CADB0533`. A name resolves in the nearest query first. Qualify columns.
- A scalar subquery with zero rows gives `NULL`. More than one row is an error.
  Read "Subqueries" in [references/statements.md](references/statements.md).
- A derived table needs an alias. Alias each computed column inside it, or list
  names after the alias: `AS d (customer, spent)`.

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
UPDATE robots SET year = 1982 WHERE name = 'T-800' RETURNING id, year;
DELETE FROM orders WHERE total > 100 LIMIT 10 RETURNING *;
UPDATE accounts AS a SET balance = a.balance + d.amount
  FROM deltas AS d WHERE d.account_id = a.id RETURNING a.id, a.balance;
DELETE FROM orders AS o USING customers AS c WHERE c.id = o.customer_id AND c.closed = true;
```

- `UPDATE` and `DELETE` require a `WHERE` clause. They accept `LIMIT n` before
  `RETURNING`. To empty a table, use `TRUNCATE [TABLE] t [RESTART IDENTITY]`: one
  table, no `CASCADE`, and not in an explicit transaction (`CADB0538`).
- `UPDATE ... FROM` and `DELETE ... USING` change only the target. An `UPDATE` fails
  with `CADB0400` when one target row matches several source rows: aggregate the
  source first. These forms refuse `LIMIT`, `RETURNING *`, and source columns in
  `RETURNING`. Read "UPDATE ... FROM" in [references/statements.md](references/statements.md).
- A multi-row `INSERT` is atomic. A repeated target column fails with `CADB0403`.
- `ON CONFLICT` takes a column list or `ON CONSTRAINT name`, then `DO NOTHING` or
  `DO UPDATE`. `DO UPDATE` must name a target. `excluded.c` is the proposed row.
  `UPSERT` updates on the primary key. Any failed statement ends an explicit
  transaction. There are no savepoints.
- `RETURNING` takes `*`, columns, scalar expressions, aliases, and parameters. It
  refuses aggregates and subqueries (`CADB0400`), sequence calls (`CADB0547`), and
  unknown columns (`CADB0404`). It needs `SELECT`. `UPDATE` returns the new row.
- A gRPC non-query reply over 4 MiB fails with `CADB0550`. Stream the rows.
- One transaction may write at most `max_mutations_per_transaction` mutations
  (default 20,000). Excess fails with `CADB0506`. Split the work.

### INSERT ... SELECT and CREATE TABLE ... AS SELECT

- `INSERT INTO t (a, b) SELECT ...` and `CREATE TABLE t AS SELECT ...` match columns
  **by position**, never by name. Counts must agree.
- CTAS copies the result shape only, and adds a leading key `id OID`. Alias every
  expression. Cast a bare `NULL`. List columns instead of `*` over a join.
- The source can read a view or a CTE. A source with `AS OF SYSTEM TIME` copies
  historical rows into the present. See [references/statements.md](references/statements.md).

## Views, databases, and inspection

- `CREATE [MATERIALIZED] VIEW v AS SELECT ...`: alias each expression. A body may not
  mix `*` with other columns, use `AS OF SYSTEM TIME`, or carry hints. It can be a set
  operation or use `WITH`. `REFRESH MATERIALIZED VIEW v` updates a materialized view.
- `CREATE VIEW` binds the body without running it. `CREATE OR REPLACE VIEW` may only
  append columns (`CADB0529`).
- Every view is read-only (`CADB0525`). A view runs with its **owner's** privileges.
- A materialized view changes only on `REFRESH` (no `CONCURRENTLY`). `DROP TABLE`
  fails while a view depends on it (`CADB0530`).
- Create a database explicitly: `CREATE DATABASE IF NOT EXISTS app;`. An unknown
  database gives `CADB0010`. `USE app;` is a `camus-cli` command only.
- `SHOW TABLES` lists tables only. Use `SHOW VIEWS` and `SHOW MATERIALIZED VIEWS`.
  `DROP INDEX idx ON t` needs the table.

## Expressions

- Cast with `CAST(x AS type)` or `x::type`. `::` binds tighter than operators:
  write `(a + b)::text`. A float cast to `INT64` rounds half away from zero.
  `::int` and `::smallint` check 32-bit and 16-bit ranges (`CADB0420`).
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
- `INTERVAL '7 days'`, `INTERVAL 3 HOUR`, and `'1 day'::interval` are intervals. A
  `DATE` or `DATETIME` ± an interval gives `DATETIME`; `ts1 - ts2` gives an interval.
  No column can store one. Read "Intervals" in [references/functions.md](references/functions.md).
- `arr[n]` reads element `n`, starting at 1. Out of range gives `NULL`.
  `x op ANY (array_or_subquery)`, `SOME`, and `ALL` take any comparison operator.
- A statement deeper than 1,000 parse levels fails with `CADB0413`. So does one with
  more than 64 nested set operations, or a `WITH` whose expansion copies more than
  250,000 parse nodes. Replace a huge `CASE` with a join.

```sql
SELECT '42'::int + 1, CAST('2026-03-15' AS DATE), (ARRAY['a', 'b'])[1 + i % 2] FROM series;
SELECT id FROM orders WHERE 'news' = ANY (tags) AND created_at > now() - INTERVAL '7 days';
```

## Functions

Read [references/functions.md](references/functions.md) for the full catalog. Key rules:

- Most functions return `NULL` for a `NULL` argument. `coalesce`, `ifnull`,
  `nvl`, `nullif`, `if`, `concat`, and `json_valid` handle `NULL` differently.
- `gen_id()`, `gen_uuid_v4()`, `gen_uuid_v7()`, `random()`, and `clock_timestamp()`
  change per call. `now()`, `current_timestamp()`, `current_date()`, and
  `unix_timestamp()` report the start of the transaction. Write the parentheses.
- Session functions (`current_user()`, ...) are not allowed in a `DEFAULT` or a
  `CHECK`. `age()` is volatile, so a `CHECK` cannot use it either.
- Times are UTC. `date_add`, `date_sub`, and `DATE + INTERVAL` return `DATETIME`.
- `sin`, `cos`, `atan2`, ... take radians. `sind`, `cosd`, `atan2d`, ... take degrees.
- Text positions and lengths count code points. Sorting uses binary UTF-16 order,
  with no `COLLATE`.

## Not supported

- `FULL [OUTER] JOIN`, `NATURAL JOIN`, `JOIN ... USING (...)`, `RIGHT JOIN LATERAL`,
  `CROSS APPLY`, `OUTER APPLY`.
- `COUNT(DISTINCT x)`, `SELECT DISTINCT ... GROUP BY`, `SELECT DISTINCT COUNT(*)`.
- A write inside a CTE, mutual recursion, `SEARCH`/`CYCLE`, `CREATE RECURSIVE VIEW`,
  a standalone `VALUES` statement, `CORRESPONDING BY`, temporary views, updatable
  views, `security_invoker`.
- `FILTER (WHERE ...)`, the `WINDOW` clause, `GROUPS` frames, `EXCLUDE`, `NULLS FIRST`,
  `ON DUPLICATE KEY UPDATE`, `MERGE`, `FOR SHARE`, `unnest`, `WITH ORDINALITY`.
- `LIMIT` in `UPDATE ... FROM` or `DELETE ... USING`, `ORDER BY` in `UPDATE` or
  `DELETE`, `INTERVAL` columns, `SUM` or `AVG` of intervals, `HOUR_MINUTE` units.
- Array slices `a[1:2]`, `UPDATE ... SET a[1] = x`, `@>` and `<@` on an array, array
  text `'{a,b}'`. `@>`, `<@`, and `'{a,b}'` paths work on `jsonb`.
- `jsonpath`, `doc['key']`, `row_to_json`, `jsonb_agg(x ORDER BY y)`, `NUMERIC(p, s)`,
  `ARRAY(NUMERIC)`, nested arrays, `regexp_matches`, `regexp_split_to_table`,
  POSIX classes such as `[[:alpha:]]`.
- `TRUNCATE a, b`, `TRUNCATE ... CASCADE`, `REFRESH ... CONCURRENTLY`.

## Common mistakes

- `"col"` as an identifier (PostgreSQL). It is a string. Use backticks.
- `'O\'Brien'` or `LIMIT 10, 20` (MySQL). Write `'O''Brien'` and `LIMIT 20 OFFSET 10`.
- `created_at + INTERVAL '7 days' > now()`. It cannot use an index. Write
  `created_at > now() - INTERVAL '7 days'`.
- `a || b` when an operand can be `NULL`. The result is `NULL`. Write `concat(a, b)`,
  which skips `NULL` arguments. `||` is not a logical OR.
- `WHERE int_col = '5'` (MySQL). It fails with `CADB0400`. Write `int_col = 5`.
- A `LIKE` pattern with a literal `_` or `\`. Escape it: `'a\_%'`.
- A plain `SELECT ... AS OF SYSTEM TIME` inside an explicit transaction. It works
  only in autocommit, or as the source of `INSERT ... SELECT` or CTAS.

## References

Read these files in this skill:

- [references/statements.md](references/statements.md): exact statement syntax,
  `ON CONFLICT`, `RETURNING`, `UPDATE ... FROM`, prepared statements, and error codes.
- [references/ctes-and-lateral.md](references/ctes-and-lateral.md): read before you
  write `WITH`, `WITH RECURSIVE`, `LATERAL`, or a `VALUES` source.
- [references/functions.md](references/functions.md): read before you call any
  scalar function, use an `INTERVAL`, or format and parse a date with a pattern.
- [references/set-and-window.md](references/set-and-window.md): `UNION`, `INTERSECT`,
  `EXCEPT`, `OVER`, and `generate_series`.
- [references/jsonb.md](references/jsonb.md): `jsonb` operators, functions, and aggregates.
- [references/expressions.md](references/expressions.md): operator precedence, `LIKE`,
  checks before rows are read, output names, `LIMIT` rules, and Unicode text.
- [references/porting-from-postgres-mysql.md](references/porting-from-postgres-mysql.md):
  read when you translate SQL from PostgreSQL, MySQL, CockroachDB, or Spanner.

Public docs, under `https://camusdb.github.io/docs/` (append the page name):

- Dialect: `sql`, `sql-expression-syntax`, `data-types` (`#intervals`), `json`,
  `statement-nesting-limit`, `functions`, `functions-*`.
- Reads: `sql-queries` (`#with-queries`, `#recursive-ctes`, `#set-operations`,
  `#window-functions`, `#for-update`), `joins-and-subqueries` (`#lateral`,
  `#values-sources`), `sql-fromless-select`, `time-travel-reads`.
- Writes: `sql-writes` (`#joined-writes`, `#on-conflict`, `#returning`),
  `insert-select-and-ctas`, `truncate-table`.
- Objects and other: `views`, `materialized-views`, `databases`, `sql-inspection`,
  `comment-on`, `prepared-statements`, `http-api`, `grpc-api`, `error-codes`,
  `settings-reference`.
