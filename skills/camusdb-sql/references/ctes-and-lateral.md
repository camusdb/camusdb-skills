# WITH queries, LATERAL, and VALUES sources

Read this file when a query names an intermediate result with `WITH`, walks a
hierarchy with `WITH RECURSIVE`, reads left columns with `LATERAL`, builds a small
inline relation with `VALUES`, or renames derived-table columns.

Source pages: https://camusdb.github.io/docs/sql-queries#with-queries,
https://camusdb.github.io/docs/sql-queries#recursive-ctes,
https://camusdb.github.io/docs/joins-and-subqueries#lateral,
https://camusdb.github.io/docs/joins-and-subqueries#values-sources, and
https://camusdb.github.io/docs/statement-nesting-limit#cte-expansion.

## WITH queries

```sql
WITH totals AS (
  SELECT customer_id, SUM(total) AS spent
  FROM orders GROUP BY customer_id
), big_customers (customer_id, spent) AS (
  SELECT customer_id, spent FROM totals WHERE spent > 1000
)
SELECT customer_id, spent FROM big_customers ORDER BY spent DESC;
```

```sql
WITH [RECURSIVE] name [(col, ...)] AS [MATERIALIZED | NOT MATERIALIZED] (query) [, ...]
{ SELECT ... | INSERT ... | UPSERT ... | UPDATE ... | DELETE ... };

INSERT INTO t [(col, ...)] WITH name AS (query) SELECT ...;
```

- The query can hold joins, aggregates, windows, set operations, or another `WITH`.
- A column list renames columns by position. A shorter list keeps the other
  names. A longer list fails. Two outputs with one name make a reference to that
  name ambiguous.
- A CTE is visible to later definitions and to the statement, its subqueries
  included.
- A CTE hides a table or view with the same name. In its own body, and in an
  earlier definition, the name still reads the table.
- An inner `WITH` can hide an outer CTE name. Two CTEs with one name in one clause
  fail.
- An index hint on a CTE reference is not supported.
- An unused definition is not checked. An unknown table in it raises no error.

### Where WITH works

- Before `SELECT`, `INSERT`, `UPSERT`, `UPDATE`, and `DELETE`.
- In `INSERT INTO ... WITH ... SELECT`, CTAS, view and materialized-view bodies,
  subqueries, derived tables, and set-operation operands.
- A write target always names a stored table. Its sources can read CTEs, also in
  `UPDATE ... FROM` and `DELETE ... USING`.

Not supported:

- A write inside a definition, such as `AS (DELETE ... RETURNING ...)`.
- `SELECT ... FOR UPDATE` that reads a CTE.
- A direct `WITH c AS (VALUES ...)` body. Write `AS (SELECT * FROM (VALUES ...) AS v)`.

### Reuse and materialization

| Case | Behavior |
| --- | --- |
| Referenced once, at the start of a statement or its source query | Inlined. The reader runs it as a derived table. |
| Referenced more than once | Computed once. The readers share its rows for the statement. |
| `MATERIALIZED` | Computed once. |
| `NOT MATERIALIZED` | Each reference runs its own copy. |
| A `WITH` inside parentheses | Always inlined, also with `MATERIALIZED`. Its query can depend on outer rows. |
| A view or materialized-view body | Each reference expands to a derived table and runs separately. |

- Readers of a computed-once CTE see the same `random()` values.
- A computed-once CTE runs in full before a reader gets rows, even under a
  `LIMIT`. Each reader filters its own copy of the kept rows. The rows can spill to
  disk and are freed when the statement ends.
- Use `NOT MATERIALIZED` when a reader reads only part of a large result.
- `EXPLAIN` marks the readers with `computed_once=<name>`.
- A stored view body shows the derived tables in `SHOW CREATE VIEW`. A
  materialized view still stores the result of each refresh.

### Expansion limit

The expansion copies each referenced body. One `WITH` clause can copy at most
250,000 parse nodes. A chain where each CTE reads the one before twice reaches the
limit in about twenty steps. Excess fails with `CADB0413`. Shorten the chain or
store an intermediate result. The 1,000-level depth limit also applies.

## WITH RECURSIVE

```sql
WITH RECURSIVE tree (id, depth) AS (
  SELECT id, 0 FROM categories WHERE parent_id IS NULL
  UNION ALL
  SELECT c.id, t.depth + 1
  FROM tree AS t JOIN categories AS c ON c.parent_id = t.id
)
SELECT id, depth FROM tree ORDER BY depth, id;
```

- The body must be `seed_query UNION [ALL] recursive_query`.
- The seed runs once. Each step reads only the rows of the step before. The
  recursion ends when a step returns no rows.
- `UNION ALL` keeps duplicates. `UNION` drops rows already returned, so it can end
  a walk over a cycle when whole rows repeat. A changing depth or path column keeps
  the rows distinct, so such a query needs an explicit stop condition.
- The recursive term references the CTE once, in its `FROM` sources, directly or
  through a derived table.
- It cannot reference the CTE in an expression subquery, on the nullable side of an
  outer join, or in the restricted operands of `INTERSECT ALL` and `EXCEPT`.
- An aggregate is refused in the `SELECT` that reads the work table directly. An
  outer `SELECT` over a derived table may aggregate. Windows, `DISTINCT`,
  `GROUP BY`, and `HAVING` work.
- The connecting `UNION` takes no `ORDER BY`, `LIMIT`, or `OFFSET`. A recursive
  term in parentheses may have them.
- Under `WITH RECURSIVE`, a definition can reference a later CTE. Its own name
  hides a table with that name. Mutual recursion between CTEs is not supported.
- The seed columns set the result types. An untyped `NULL` or a bare parameter in
  the seed becomes `STRING`. Cast a seed expression when the recursive term would
  widen its type.
- A single reader streams rows as the steps produce them, so its `LIMIT` can stop
  the recursion early. With several readers, the whole result is computed first,
  and an outer `LIMIT` does not stop the recursion.
- Work tables can spill to disk. The duplicate set of `UNION` stays in memory.

| Setting | Default | Limit |
| --- | ---: | --- |
| `max_recursive_cte_iterations` | 10000 | Recursive steps that produce rows. |
| `max_recursive_cte_rows` | 10000000 | Output rows per run, seed rows included. |

- Past either limit, the statement fails with `CADB0560`
  `RecursiveQueryLimitExceeded` (HTTP 400). A retry reaches the same limit. Add a
  stop condition, or change the limit after you check the query.
- Both are runtime cluster settings. `0` turns a limit off.
- `EXPLAIN` shows `recursive-union` and `work-table-scan`. `EXPLAIN (ANALYZE)` does
  not count the rows of each term separately.
- Not supported: the PostgreSQL `SEARCH` and `CYCLE` clauses, and
  `CREATE RECURSIVE VIEW`. A view body can use `WITH RECURSIVE`.

## LATERAL

```sql
SELECT c.id, o.id, o.total
FROM customers AS c
CROSS JOIN LATERAL (
  SELECT id, total FROM orders
  WHERE customer_id = c.id ORDER BY total DESC LIMIT 2
) AS o;

SELECT t.id, n FROM tasks AS t, generate_series(1, t.repetitions) AS n;
```

- A `LATERAL` source reads columns of the sources to its left.
- The comma, `CROSS JOIN`, `INNER JOIN`, and `LEFT JOIN` forms work.
- `LEFT JOIN LATERAL (...) AS o ON true` keeps a left row whose lateral query
  returns no rows. The right columns are `NULL`.
- `RIGHT JOIN LATERAL` is refused. `CROSS APPLY` and `OUTER APPLY` are not
  supported.
- A name resolves in the lateral query first, then in the left sources, then in
  the enclosing queries. A lateral source cannot read a source to its right. A
  second lateral source can read the first one.
- Without `LATERAL`, a derived table cannot read other items of its own `FROM`.
  A derived table and a `VALUES` list need an explicit `LATERAL` to read left
  columns.
- A table-function argument can read left columns without the keyword. This works
  for `generate_series`, `jsonb_each(t.payload)` and the other `jsonb_*` table
  functions, and extension table functions. The function then runs for each left
  row. An argument without column references runs once.
- The planner keeps the written join order and uses nested loops for lateral
  sources. `EXPLAIN` marks the join `lateral`.
- Repeated left values can share a small result within the statement. A volatile
  body runs again for each left row.
- Views, `INSERT ... SELECT`, CTAS, `UPDATE ... FROM`, and `DELETE ... USING` can
  use lateral sources.
- `LATERAL` is reserved. Quote a name `lateral` with backticks.

## VALUES sources

```sql
SELECT p.id, v.label
FROM products AS p
JOIN (VALUES (1, 'featured'), (3, 'clearance')) AS v (id, label) ON v.id = p.id;

SELECT p.id, v.dimension, v.size
FROM packages AS p
CROSS JOIN LATERAL (VALUES ('width', p.width), ('height', p.height)) AS v (dimension, size);
```

- The alias is required. Without a column list, the columns are `column1`,
  `column2`, and so on.
- A shorter column list renames the first columns. Too many names, or rows of
  unequal width, fail.
- An item can be an expression, a parameter, a function call, or a scalar
  subquery. `DEFAULT`, built-in aggregates, and window calls are refused. An
  extension aggregate is evaluated over the single row of its item.
- Column types follow the set-operation rules in
  [set-and-window.md](set-and-window.md): integers and floats widen, text with a
  number fails, and an all-`NULL` column stays untyped.
- Only `ORDER BY` fixes the row order.
- A `VALUES` source works in `SELECT`, joins, subqueries, CTEs, views, joined
  writes, `INSERT ... SELECT`, and CTAS. `SHOW CREATE VIEW` keeps the `VALUES`
  syntax.
- Not supported: a standalone `VALUES (1), (2)` statement and a direct
  `WITH c AS (VALUES ...)` body. Write `SELECT * FROM (VALUES ...) AS v`. `ORDER BY`,
  `LIMIT`, and `OFFSET` cannot appear inside the `VALUES` parentheses.
- A list runs as a derived table over `UNION ALL`. For thousands of reused rows,
  use a stored table. A source-only `EXPLAIN` shows `values(alias=v, rows=N)`.

## Derived-table column lists

```sql
SELECT d.customer, d.spent
FROM (SELECT customer_id, SUM(total) FROM orders GROUP BY customer_id) AS d (customer, spent);
```

A column list after the alias renames the output columns by position. A shorter
list renames only the first columns. A longer list is refused. Duplicate names make
a reference ambiguous.
