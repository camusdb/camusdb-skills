# Set operations, window functions, and series

Read this file when a query combines result sets, computes a value across related
rows, or generates rows in `FROM`.

Source pages: https://camusdb.github.io/docs/sql-queries#set-operations,
https://camusdb.github.io/docs/sql-queries#window-functions, and
https://camusdb.github.io/docs/functions-set-returning.

## Set operations

```sql
q1 UNION [ALL | DISTINCT] q2
q1 INTERSECT [ALL | DISTINCT] q2
q1 EXCEPT [ALL | DISTINCT] q2

(SELECT a FROM t ORDER BY a LIMIT 5) UNION ALL SELECT b FROM u ORDER BY 1 LIMIT 10 OFFSET 2;
```

Two rows are the same when their values are equal by position. Two `NULL` values
are equal here, as in `DISTINCT` and `GROUP BY`. The default is `DISTINCT`.

For a row that the left query returns m times and the right query n times:

| Form | Copies returned |
| --- | --- |
| `UNION ALL` | m + n |
| `UNION` | 1 if m + n > 0 |
| `INTERSECT ALL` | min(m, n) |
| `INTERSECT` | 1 if m > 0 and n > 0 |
| `EXCEPT ALL` | max(m - n, 0) |
| `EXCEPT` | 1 if m > 0 and n = 0 |

Without `ORDER BY`, the row order is not defined.

### Precedence

`INTERSECT` binds tighter than `UNION` and `EXCEPT`. `UNION` and `EXCEPT` share a
level and group from left to right. Parentheses override both.

```sql
a UNION b INTERSECT c      -- a UNION (b INTERSECT c)
a EXCEPT b EXCEPT c        -- (a EXCEPT b) EXCEPT c
a UNION b EXCEPT c         -- (a UNION b) EXCEPT c
```

### Columns and types

- Both operands must return the same column count, or the statement fails with
  `CADB0400`.
- Columns match by position. Output names come from the leftmost operand.
- Each output column gets one type:

| Left and right | Output type |
| --- | --- |
| The same type | That type. |
| A `NULL` literal or a placeholder on one side | The other side's type. |
| Two numeric types | The type `+` gives: `int64` with `float64` is `float64`. `int64` with `numeric` is `numeric`. |
| `date` with `datetime` | `datetime`. |
| A quoted string literal with `date` or `datetime` | That temporal type. A bad literal fails before any read. |
| Two arrays | An array, when the element types agree or one side is `ARRAY[]`. |
| Any other pair | `CADB0400`. |

Each value converts to the output type before rows compare.
`SELECT 1 UNION SELECT 1.0` returns one row, `1.0`. A `string` column with a `date`
column fails. Only a literal in the operand's own select list converts.

### ORDER BY, LIMIT, and OFFSET

- After the last operand, these clauses apply to the combined result.
- A combined `ORDER BY` item must be an output name or a position (`ORDER BY 1`).
- An expression such as `ORDER BY a + 1` fails. Use a derived table:
  `SELECT * FROM (q1 UNION q2) AS s ORDER BY s.a + 1`.
- An operand takes its own clauses only inside parentheses.
- `(SELECT a FROM t LIMIT 5) ORDER BY a` fails. Put all clauses inside the
  parentheses, or wrap the query in a derived table.

### Where a set operation can appear

- As a statement, and under `EXPLAIN`.
- In a derived table, and in a subquery: `x IN (q1 UNION q2)`,
  `EXISTS (q1 EXCEPT q2)`, or a scalar subquery.
- As the source of `INSERT INTO t SELECT ... UNION SELECT ...` and CTAS. A source
  that starts with a parenthesis also works.
- As a view or materialized-view body. The `*` of each operand expands at creation.
  The leftmost operand names the columns, so alias its expressions.
- An operand can read a view.

Rules:

- A set operation inside a derived table or subquery of a view body cannot use `*`.
- All operands read one snapshot. An operand with its own `AS OF SYSTEM TIME` fails
  with `InvalidAsOfSystemTime`.
- Each operand needs `SELECT` on the tables it reads.
- `FOR UPDATE` over a set operation fails with `CADB0400`.
- A `WITH` query can name a set-operation result, and an operand can hold a `WITH`.
  A recursive CTE joins its seed and step with `UNION [ALL]`. See
  [ctes-and-lateral.md](ctes-and-lateral.md).
- `CORRESPONDING BY` is not supported.
- A chain of `UNION ALL`, `UNION`, `INTERSECT`, or `INTERSECT ALL` is rebalanced, so
  a thousand operands run. `EXCEPT` and mixed chains add one level per operator. A
  statement nests at most 64 set operations (`CADB0413`).
- The query result cache never stores a set-operation result.

## Window functions

```sql
-- Top three orders per customer
SELECT * FROM (
  SELECT customer, id, total,
         row_number() OVER (PARTITION BY customer ORDER BY total DESC, id) AS rn
  FROM orders
) ranked
WHERE rn <= 3;

-- Running total and the change from the previous day
SELECT day, amount,
       sum(amount) OVER (ORDER BY day) AS running,
       amount - lag(amount, 1, 0) OVER (ORDER BY day) AS change
FROM daily_sales;
```

### Syntax

```sql
function(args) OVER ( [PARTITION BY expr [, ...]]
                      [ORDER BY expr [ASC | DESC] [, ...]]
                      [frame] )

frame := { ROWS | RANGE } start
       | { ROWS | RANGE } BETWEEN start AND end

start, end := UNBOUNDED PRECEDING | n PRECEDING | CURRENT ROW
            | n FOLLOWING | UNBOUNDED FOLLOWING
```

- `OVER ()` makes the whole result one partition.
- `n` is a non-negative integer literal or a placeholder. Only `ROWS` accepts it.
- A window call is allowed in the select list and the final `ORDER BY` only. It can
  sit inside a larger expression: `coalesce(lag(v) OVER (ORDER BY id), 0)`.
- Arguments and the specification can use group keys and aggregates of a grouped
  query: `rank() OVER (ORDER BY sum(amount) DESC)` with `GROUP BY customer`.
- `OVER` is reserved. Quote it with backticks to use it as a name. `PARTITION`,
  `ROWS`, `RANGE`, `GROUPS`, `UNBOUNDED`, `PRECEDING`, `FOLLOWING`, `CURRENT`,
  `ROW`, and `EXCLUDE` stay plain identifiers.

### Functions

| Function | Result type | Value for each row |
| --- | --- | --- |
| `row_number()` | `INT64` | Position in the partition, from 1. |
| `rank()` | `INT64` | Peers share a rank, and a gap follows. |
| `dense_rank()` | `INT64` | Peers share a rank, with no gaps. |
| `percent_rank()` | `FLOAT64` | `(rank - 1) / (rows - 1)`; 0 for one row. |
| `cume_dist()` | `FLOAT64` | Rows up to the last peer, divided by rows. |
| `ntile(k)` | `INT64` | Bucket of `k`, larger buckets first. `k` must exceed 0. |
| `lag(x [, offset [, default]])` | type of `x` | `x` of the row `offset` before (default 1), or `default` (default `NULL`). |
| `lead(x [, offset [, default]])` | type of `x` | `x` of the row `offset` after, or `default`. |
| `first_value(x)` | type of `x` | `x` of the first frame row. |
| `last_value(x)` | type of `x` | `x` of the last frame row. |
| `nth_value(x, n)` | type of `x` | `x` of frame row `n`, or `NULL`. `n` must exceed 0. |
| `count(*)`, `count(x)` | `INT64` | Frame rows, or frame rows where `x` is not `NULL`. |
| `sum(x)`, `avg(x)`, `min(x)`, `max(x)` | as the plain aggregate | The aggregate over the frame. |

- Ranking functions, `lag`, and `lead` ignore a frame.
- `lag` and `lead` read `offset` and `default` on the current row. A negative offset
  reads in the other direction. The default must match `x`, be `NULL`, or both must
  be numeric. `lag(int_col, 1, 0.5)` is a `FLOAT64` column on every row.
- An empty frame gives `NULL`, and `0` for `count`.
- `sum` of `INT64` past the range fails with `CADB0420`. A float frame total past
  the range fails with `CADB0421`.
- `sum(x) OVER ()` and plain `sum(x)` can differ in the last binary digit of a float.

### Frames and peers

Rows with equal window `ORDER BY` values are peers. With no `ORDER BY`, every row of
the partition is a peer of every other row.

The default frame is `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`. In a
`RANGE` frame, `CURRENT ROW` means the first peer at the start and the last peer at
the end. So:

- `sum(x) OVER (ORDER BY day)` gives one value to every row of a day.
- `sum(x) OVER (PARTITION BY g)` is the partition total on every row.
- `last_value(x) OVER (ORDER BY t)` returns the last **peer**, not the last row of
  the partition. Write the frame to get the last row:

```sql
SELECT last_value(x) OVER (ORDER BY t ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
FROM samples;
```

`ROWS BETWEEN 2 PRECEDING AND 1 FOLLOWING` covers at most four rows.

### Order and NULL

- `NULL` sorts first ascending and last descending, as in the query's `ORDER BY`.
  PostgreSQL does the opposite by default, so a ranking can differ.
- `NULLS FIRST` and `NULLS LAST` are not supported.
- `PARTITION BY` puts all `NULL` keys in one partition.
- `row_number()` numbers peers in no defined order. Add a unique column to the window
  `ORDER BY` when the order must repeat.

### Where windows run

Windows run after `WHERE`, `GROUP BY`, and `HAVING`, and before the select list,
`DISTINCT`, `ORDER BY`, and `LIMIT`. So:

- Filter a window result through a derived table or a view.
- `LIMIT` never cuts the rows a window reads. `SELECT ..., count(*) OVER () FROM t
  LIMIT 5` returns five rows, each with the count of the whole table.

### Not supported

| Form | Error |
| --- | --- |
| A window call in `WHERE`, `GROUP BY`, `HAVING`, or `JOIN ... ON` | `CADB0400` |
| A window call inside another window call | `CADB0400` |
| A window call in `UPDATE ... SET`, `INSERT ... VALUES`, or `CHECK` | `CADB0400` |
| `RANGE` with `n PRECEDING` or `n FOLLOWING` | `CADB0400`; use `ROWS` |
| `GROUPS` frames, `EXCLUDE` | `CADB0400` |
| `OVER name` | `CADB0400` |
| `WINDOW name AS (...)`, `FILTER (WHERE ...)` | Syntax error |
| A window call in a column `DEFAULT` or a `BETWEEN` bound | Syntax error |
| A function not in the table, including extension aggregates | `CADB0400` |
| `SELECT ... FOR UPDATE` with a window | `CADB0400` |
| `SELECT DISTINCT * ... ORDER BY <window call>` | `CADB0400` |

An uncorrelated subquery can read a window query, also from an outer select list.
A select list that holds a window call refuses a correlated subquery (`CADB0533`).
A correlated `EXISTS` body can use window functions.

## Set-returning functions in FROM

A set-returning function acts as a relation in `FROM`. This section covers
`generate_series`. The `jsonb_*` set-returning functions are in [jsonb.md](jsonb.md).

```sql
INSERT INTO scores (id, score)
  SELECT i, i * 10 FROM generate_series(1, 10000) AS i;

SELECT COUNT(*) FROM generate_series(1, 1000000);
```

### generate_series(start, stop [, step])

Arguments and the output are `INT64`. The default step is 1.

| Call | Rows |
| --- | --- |
| `generate_series(1, 5)` | 1, 2, 3, 4, 5 |
| `generate_series(5, 1, -2)` | 5, 3, 1 |
| `generate_series(1, 4, 2)` | 1, 3 |
| `generate_series(1, 0)` | No rows. |
| `generate_series(1, NULL)` | No rows. |
| `generate_series(1, 5, 0)` | Error: the step cannot be zero. |

- Overflow ends the series. It does not wrap.
- An argument is a literal, a parameter, an operator expression, or a scalar
  function call. An argument without column references is evaluated once, before
  the first row.
- An argument can read columns of `FROM` sources to its left, with or without the
  `LATERAL` keyword. The function then runs for each left row:
  `SELECT t.id, n FROM tasks AS t, generate_series(1, t.repetitions) AS n;`. See
  [ctes-and-lateral.md](ctes-and-lateral.md).
- A non-integer value is refused.

### Names

| `FROM` clause | Relation | Column |
| --- | --- | --- |
| `generate_series(1, 3)` | `generate_series` | `generate_series` |
| `generate_series(1, 3) AS i` | `i` | `i` |
| `generate_series(1, 3) i` | `i` | `i` |
| `generate_series(1, 3) AS t(n)` | `t` | `n` |

A column-alias list names exactly one column. Qualify a name that a joined table also
has: `SELECT items.id, id.id FROM items, generate_series(1, 2) id;`.

### Where it can appear

- Alone, in a comma join, or on either side of any join.
- With `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY`, `DISTINCT`, `LIMIT`, and `OFFSET`.
- In a derived table, a view body, a materialized view, and an `IN` or `EXISTS`
  subquery.
- As the source of `INSERT ... SELECT` and CTAS. That write stays under the
  transaction mutation limit (`CADB0506`).

Rows stream, so `LIMIT 10` over a huge series returns at once. The function reads no
table, takes no lock, and needs no privilege. Rows come in series order. After a
join, order needs an `ORDER BY`.

A buffered endpoint caps a large result with `CADB0556`. Use the streaming endpoint,
or page the result.

### Not supported

| Form | Result |
| --- | --- |
| `SELECT generate_series(1, 3)` | `CADB0533`: only in `FROM`. |
| A timestamp, date, or `numeric` series | Error: arguments must be integers. |
| `WITH ORDINALITY` | Parse error. |
| `unnest`, `generate_subscripts`, `regexp_matches`, `regexp_split_to_table` in `FROM` | `CADB0533`. |
| A scalar function in `FROM`, such as `FROM upper('a')` | `CADB0400`. |
| An unknown name, such as `FROM no_such_function(1)` | `CADB0400`. |
| An outer name of a correlated `EXISTS` body inside a table-function argument | Not supported. |
