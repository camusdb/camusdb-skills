# Expressions, statement checks, and text rules

Read this file when an expression mixes operators, when you write a `LIKE`
pattern, when a statement fails before it reads rows, or when text holds
characters outside ASCII.

Source pages: https://camusdb.github.io/docs/sql-expression-syntax,
https://camusdb.github.io/docs/data-types (`#errors-before-reading-rows`,
`#characters-and-valid-unicode`), and https://camusdb.github.io/docs/sql-queries.

## Characters and statement boundaries

- An unknown character outside a literal, a quoted identifier, or a comment
  fails with `CADB0406`. The error gives the line and the column. An unexpected
  backslash or a bare `$` is an error.
- Dollar quotes (`$$ ... $$`, `$tag$ ... $tag$`) are accepted only for the C#
  body of an inline function or trigger. See the `camusdb-csharp-extensions` skill.
- A non-breaking space (U+00A0) or a byte-order mark (U+FEFF) is not white
  space. Check copied SQL when an invisible character causes a syntax error.
- White space is a space, a tab, a line break, a form feed, or a vertical tab.
- An unquoted identifier uses ASCII letters, digits, and `_`.
- A raw line break or tab inside a literal or a quoted identifier is refused.
  Write an escape string: `E'line\nnext'`.
- One server request holds one statement. Repeated trailing semicolons are
  accepted, but a second statement is not. A client that runs a script splits it
  into separate requests.

## Operator precedence

Operators bind from tightest to loosest. Use parentheses in a mixed expression.

| Operators | Grouping |
| --- | --- |
| `::`, array subscripts | Left |
| Unary `-`, prefix `~` | Right |
| `^` | Left |
| `*`, `/`, `%` | Left |
| `+`, `-` | Left |
| `\|\|`, bitwise (`&`, `\|`, `#`, `<<`, `>>`), regex (`~`, `~*`, `!~`, `!~*`), and `jsonb` operators | Left, all at one level |
| `BETWEEN`, `IN`, `LIKE`, `ILIKE`, and their `NOT` forms | Non-associative |
| `<`, `>`, `<=`, `>=`, `=`, `<>`, `!=` | Non-associative |
| `IS` | Non-associative |
| `NOT` | Right |
| `AND` | Left |
| `OR` | Left |

This follows PostgreSQL. One difference: prefix `~` binds like unary minus.

Interval literals such as `now() - INTERVAL '7 days'` follow the arithmetic rules in
"Intervals" in [functions.md](functions.md).

```sql
SELECT 1 + 1 IS NULL;          -- (1 + 1) IS NULL
SELECT 6 | 1 & 3;              -- (6 | 1) & 3, gives 3
SELECT 'x' || (payload ->> 'kind') FROM events;  -- parenthesize the extraction
```

- A comparison chain such as `a = b = c` fails with `CADB0406`. Join explicit
  comparisons with `AND`.
- `'x' || payload ->> 'kind'` means `('x' || payload) ->> 'kind'`.
- `payload -> 0 + 1` selects element 1, because arithmetic binds tighter.
- A stored view or `CHECK` keeps its earlier grouping, because its saved SQL holds
  parentheses. An old `a = b IS NULL` can stay `a = (b IS NULL)`. Inspect
  `SHOW CREATE VIEW` or `SHOW CREATE TABLE`. Recreate the definition to get the
  new grouping.

### Power

`a ^ b` is the same as `pow(a, b)` and returns `FLOAT64`. It is power, not XOR.
Use `#` for XOR.

| Expression | Result | Why |
| --- | --- | --- |
| `2 * 3 ^ 2` | 18 | Power binds before multiplication. |
| `-2 ^ 2` | 4 | Unary minus binds before power: `(-2) ^ 2`. |
| `2 ^ 3 ^ 2` | 64 | Powers group left to right: `(2 ^ 3) ^ 2`. |

Write `-(2 ^ 2)` for -4 and `2 ^ (3 ^ 2)` for 512. NULL and numeric errors follow
`pow`.

## LIKE and ILIKE

- `LIKE` matches the whole string. `%` matches any run of characters, also none.
  `_` matches one Unicode code point, also an emoji.
- `ILIKE` ignores case, also for cased letters outside ASCII. A newline is an
  ordinary character in both forms.
- A backslash escapes the next pattern character. A plain string keeps the
  backslash. An escape string needs a doubled backslash.
- `ESCAPE` works with `LIKE`, `ILIKE`, `NOT LIKE`, and `NOT ILIKE`. It takes one
  code point, `''` to turn escaping off, or `NULL` for an unknown result. It can be
  a parameter or a column. A longer escape string fails with `CADB0400`.
- A pattern that ends with its escape character fails with `CADB0400` only when
  the match reaches that character. `'b' LIKE 'a\'` is false. `'a\' LIKE 'a\'` fails.
- The parser stores `ESCAPE` as `like_escape(pattern, escape)`. `SHOW CREATE` and
  `EXPLAIN` can show that call. `ESCAPE` is still a valid identifier.
- `SHOW ... LIKE` uses the same rules. Escape a literal `_`:
  `SHOW VARIABLES LIKE 'ttl\_%'`.

```sql
SELECT 'a_' LIKE 'a\_';              -- true
SELECT 'ab' LIKE 'a_';               -- true
SELECT 'a%' LIKE E'a\\%';            -- true
SELECT 'a_' LIKE 'a#_' ESCAPE '#';   -- true
SELECT 'a\b' LIKE 'a\b' ESCAPE '';   -- true; escaping is off
```

**A stored pattern takes the new rules on its next evaluation.** Older builds
read `_` and a backslash as literal characters. A `CHECK` can now reject a value
that it accepted before, and a view can return other rows. A materialized view
keeps its rows until a refresh. Escape literal `_` and `\` in stored patterns.
Servers of mixed versions can disagree during an upgrade.

## IN-list values

An `IN` or `NOT IN` list holds literals, parameters, and columns:

```sql
SELECT id FROM orders WHERE status IN (preferred_status, 'pending');
```

- A column item reads the current row.
- A constant list can become an index probe. A list with a column runs per row
  and never becomes an index seek.
- An expression item, such as `IN (quantity + 1, 2)`, is a syntax error. Use a
  subquery or explicit comparisons.

## Errors before reading rows

A mistake whose types are known from literals, casts, columns, or operators fails
before any row is read. It fails on an empty table, under `LIMIT 0` or
`WHERE FALSE`, and in a branch that never runs:

- An unknown function (`CADB0407`).
- A wrong argument count (`CADB0400`).
- An operator or comparison of incompatible known types (`CADB0400`).
- A known invalid assignment (`CADB0401`). A table with a `BEFORE` trigger checks
  the assignment after the trigger, which can replace the value.

`WHERE`, `HAVING`, `JOIN ON`, and `CHECK` conditions must be boolean or `NULL`.
Numbers and text are not truthy. `WHERE 1` and `WHERE 'true'` fail with `CADB0400`.
Write `WHERE TRUE` to match every row. The check covers writes, subqueries, CTAS,
views, and new `CHECK` definitions.

A division with constant operands and a zero divisor fails with `CADB0400` before
any row is read:

- `SELECT 1 / 0 WHERE FALSE` is an error.
- A branch that is proven unused is skipped: `IF(TRUE, 1, 1 / 0)` returns 1.
- A row-dependent condition cannot hide a constant error. Guard a row's divisor:
  `CASE WHEN quantity <> 0 THEN 1 / quantity END`.
- Columns, parameters, function calls (also `MOD` and `%`), and subquery results
  are not constant divisions for this check.
- A stored view or `CHECK` defers a constant division error until use. CTAS and
  `CREATE MATERIALIZED VIEW` run the query at once.

Some checks still wait for values: parameters, some function argument types,
`JSON_VALUE`, and some derived results.

## Output column names

- `AS` names an output column. A bare column keeps its name.
- A scalar subquery takes the name of its inner output column.
- Every other unaliased expression, such as a function call or an aggregate, takes
  its zero-based select-list position as its name. PostgreSQL can use the function
  name or `?column?`. Alias each computed result that a query, a view, or a client
  reads by name.

## ORDER BY, LIMIT, and OFFSET

- An `ORDER BY` alias wins over a base column with the same name.
- An `ORDER BY` position counts expanded columns. In
  `SELECT *, year + 1 AS next_year FROM robots ORDER BY 2`, `2` is the second column
  that `*` produces, not the second written item.
- Two output columns with the same name and different expressions make a bare
  `ORDER BY name` ambiguous. It fails with `CADB0400`, also with `LIMIT 0`. Use
  unique aliases or a 1-based position such as `ORDER BY 2`. The same input
  column repeated in the output is not ambiguous.
- A global aggregate (an aggregate without `GROUP BY`) refuses a bare input column
  in `ORDER BY`. An output alias, an aggregate expression, or a position is
  accepted, and the single row is not sorted.
- `LIMIT` and `OFFSET` counts are nonnegative integers. A negative literal fails at
  bind. A negative parameter fails when the clause is read. Both give `CADB0400`.
  The rule also covers nested queries, `SELECT` without `FROM`, and `UPDATE` or
  `DELETE` with `LIMIT`.

## Text and Unicode

- Text lengths, substring positions, `POSITION`/`strpos`, trim character sets, and
  regex start and result positions count Unicode code points. `length('x🙂y')` is 3,
  and `substring('x🙂y', 2, 1)` is `🙂`.
- Combining marks count separately. The count is not a count of shown glyphs.
- `STRING(N)`, `CHAR(N)`, and `VARCHAR(N)` hold at most `N` code points. An emoji
  fits in `CHAR(1)`.
- `CHAR(N)` neither pads nor trims spaces. `'ab'` differs from `'ab '`, and
  `'abc '` exceeds `CHAR(3)`. PostgreSQL `CHAR` differs.
- Comparisons, sorts, `MIN`/`MAX`, and indexes use binary UTF-16 code-unit order.
  There is no locale collation and no `COLLATE` clause. Use `lower` for a
  comparison that ignores case.
- Regex matching works on UTF-16 units. `regexp_count('🙂', '.')` is 2, and
  `regexp_like('🙂', '^.$')` is false. Name the character in the pattern when you
  can: `regexp_replace('a🙂', '🙂', 'b')` returns `ab`.
- A lone half of a UTF-16 surrogate pair is invalid text. A SQL string parameter or
  string-array element that holds one fails with `CADB0400` before execution, even
  when the statement does not use it. The message names the parameter and the
  position. A typed row write also refuses it and names the column.
- Stored replacement characters (U+FFFD) stay as they are. A Protobuf client can
  replace a surrogate half with U+FFFD before it sends a gRPC request. The server
  cannot detect this. Validate text at the application boundary.
