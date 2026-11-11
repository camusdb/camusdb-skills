# Behavior changes after a server upgrade

Sources: https://camusdb.github.io/docs/data-types,
https://camusdb.github.io/docs/sql-expression-syntax,
https://camusdb.github.io/docs/check-constraints,
https://camusdb.github.io/docs/sequences,
https://camusdb.github.io/docs/views,
https://camusdb.github.io/docs/insert-select-and-ctas

Read this file before you upgrade a server that holds data, and when a stored
view, `CHECK`, or column acts differently after an upgrade. Rehearse the
upgrade on a branch or a restored copy, and inspect the objects below.

## Contents

- Rolling upgrades
- Integer columns
- LIKE patterns in stored objects
- Operator precedence in stored objects
- Float-to-integer casts in CHECK constraints
- New CHECK validation
- Statement checks that now fail
- Views as copy sources

## Rolling upgrades

Keep every node on the same version when results must agree. During a rolling
upgrade, nodes with mixed versions can disagree on `LIKE` matches and on
float-to-integer rounding. A materialized view keeps its stored rows until
`REFRESH`, so it keeps old results after the upgrade.

## Integer columns

New declarations now enforce their width:

| Declaration | Accepted range | Schema output |
| --- | --- | --- |
| `SMALLINT` | -32768 to 32767 | `SMALLINT` |
| `INT`, `INTEGER` | -2147483648 to 2147483647 | `INT` |
| `INT64`, `BIGINT` | The full signed 64-bit range | `INT64` |

- A write or cast out of the declared range fails with `CADB0420` (HTTP 400,
  gRPC `OUT_OF_RANGE`). This covers inserts, updates, upserts, row APIs,
  defaults, added-column fills, and `ARRAY(SMALLINT)` or `ARRAY(INT)` elements.
- **Existing columns keep their old range.** Before this change, `INT` and
  `SMALLINT` were unbounded `INT64` aliases. Those columns still accept any
  `INT64` value and render as `INT64`.
- Old stored views and `CHECK` bodies keep their normalized `INT64` casts.
- `CREATE TABLE ... AS SELECT` gives `INT64` integer columns, even from a
  narrower source.
- `SERIAL` and `BIGSERIAL` stay `INT64`. A sequence on a narrow column keeps its
  own bounds. A generated value out of the column range fails the write with
  `CADB0420`.

Use `BIGINT` or `INT64` in a migration when values can exceed the 32-bit range.

## LIKE patterns in stored objects

`LIKE` now follows the PostgreSQL rule: `_` matches one character, and a
backslash escapes the next pattern character. `ESCAPE` selects another escape
character. Older builds treated `_` and backslash as literals.

**Existing stored patterns take the new rules on their next evaluation.**

- A `CHECK` can now reject a value that it accepted before.
- A view can return different rows.
- A materialized view keeps its rows until `REFRESH`.

Find stored patterns with `SHOW CREATE TABLE` and `SHOW CREATE VIEW`. Escape
literal underscores and backslashes where needed:

```sql
SELECT 'a_' LIKE 'a\_';               -- true: the underscore is literal
SELECT 'a_' LIKE 'a#_' ESCAPE '#';     -- true
```

## Operator precedence in stored objects

Operators now follow the PostgreSQL precedence table. The JSONB operators share
the level of `||`, and `IS` binds less tightly than comparisons.

Stored views and `CHECK` constraints keep their earlier grouping, because their
saved SQL contains parentheses. For example, an old `a = b IS NULL` can stay
`a = (b IS NULL)`. Inspect `SHOW CREATE VIEW` and `SHOW CREATE TABLE`. Recreate
a definition if you want the new grouping.

## Float-to-integer casts in CHECK constraints

`CAST`, `::`, and `to_int64` now round a `FLOAT64` or `FLOAT32` halfway value away
from zero: `CAST(2.5 AS INT64)` is 3. Stored `CHECK` expressions use the new
rounding for later writes. Materialized views keep their values until refreshed.

## New CHECK validation

`CREATE TABLE` and `ADD CONSTRAINT` now refuse a non-boolean condition, an
unknown function, a wrong argument count, or a known operand-type mismatch,
before they scan rows. Loading an existing stored check does not validate it
again. A type error reached while a write evaluates an existing check still
fails with `CADB0303`.

## Statement checks that now fail

A migration script that ran on an older build can now fail before it reads a
row, even on an empty table:

- A `WHERE`, `HAVING`, or `JOIN ON` condition that is not boolean, such as
  `WHERE 1`. Use `WHERE TRUE`.
- A constant division by zero, such as `SELECT 1 / 0 WHERE FALSE`.
- A negative `LIMIT` or `OFFSET`.
- A bare `ORDER BY` name that two different output columns share.
- An unknown character outside a literal, such as a non-breaking space copied
  into the script (`CADB0406`, with line and column).
- A second statement after a semicolon in one request. Split the script into
  separate requests.

Fix the script. These errors are not retryable.

## Views as copy sources

A plain view can now be the source of `CREATE TABLE ... AS SELECT` (also with
`WITH NO DATA`), `INSERT ... SELECT`, or a materialized view body. A write to the
view itself still fails with `CADB0525`.

```sql
CREATE TABLE open_copy AS SELECT id, customer FROM open_orders;
INSERT INTO archived_orders (id, customer) SELECT id, customer FROM open_orders;
```

A scalar subquery column keeps its declared type in CTAS, even when the
subquery has no row. Give it an alias:

```sql
CREATE TABLE payment_summary AS
SELECT (SELECT MAX(amount) FROM payments) AS largest_payment;
```

A plain untyped `SELECT NULL AS x` is still refused.
