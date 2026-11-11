# CamusDB data types

Read this file when you choose a column type, write a literal, or check a
length limit. Source: https://camusdb.github.io/docs/data-types,
https://camusdb.github.io/docs/json, and https://camusdb.github.io/docs/storage.

## Type table

| SQL type | Stores | Stored size | Indexable | `DESC` in an index |
| --- | --- | --- | --- | --- |
| `OID` | A 12-byte ObjectId | 12 bytes | Yes | Yes |
| `UUID` | A 128-bit UUID | 16 bytes | Yes | Yes |
| `INT64`, `BIGINT` | A 64-bit signed integer | 8 bytes | Yes | Yes |
| `INT`, `INTEGER` | A signed 32-bit range, stored as `INT64` | 8 bytes | Yes | Yes |
| `SMALLINT` | A signed 16-bit range, stored as `INT64` | 8 bytes | Yes | Yes |
| `SERIAL`, `BIGSERIAL` | An `INT64` identity column | 8 bytes | Yes | Yes |
| `NUMERIC` | An exact decimal, precision 38, scale 9 | Fixed-point | Yes | See note |
| `FLOAT64` | An IEEE-754 double | 8 bytes | Yes | Yes |
| `FLOAT32` | An IEEE-754 single | 4 bytes | Yes | Yes |
| `BOOL` | A boolean | 1 byte | Yes | Yes |
| `STRING`, `STRING(N)` | Unicode text | Length prefix + text | Yes | No |
| `DATE` | A calendar date, UTC ticks at midnight | Ticks | Yes | Yes |
| `DATETIME` | An instant in UTC | Ticks | Yes | Yes |
| `BYTES`, `BYTES(N)` | Opaque bytes | Length prefix + bytes | Yes | No |
| `ARRAY(T)` | An ordered list of scalar `T` | Element type + elements | No | No |
| `JSONB` | One JSON document | One format byte + canonical CBOR | GIN only | No |

Note: the docs list the `DESC` types as `OID`, `UUID`, `INT64`, `FLOAT64`,
`FLOAT32`, `BOOL`, `DATE`, and `DATETIME`. They do not name `NUMERIC`. Do not
promise a `DESC` `NUMERIC` index column without a test.

A `DESC` `STRING` or `BYTES` index column fails with `InvalidInput`. This
applies to primary keys and secondary indexes.

## Aliases

| Alias | Canonical type |
| --- | --- |
| `BIGINT` | `INT64` |
| `DECIMAL` | `NUMERIC` |
| `FLOAT`, `DOUBLE PRECISION` | `FLOAT64` (`DOUBLE` only inside a `CAST`) |
| `REAL` | `FLOAT32` |
| `TIMESTAMP` | `DATETIME` |
| `BLOB`, `BLOB(N)` | `BYTES`, `BYTES(N)` |
| `CHAR`, `VARCHAR`, `TEXT`, `CHAR(N)`, `VARCHAR(N)` | `STRING`, `STRING(N)` |
| `OBJECT_ID` | `OID` |
| `GUID` | `UUID` |
| `BOOLEAN` | `BOOL` |
| `JSON` | `JSONB` |

`INT`, `INTEGER`, and `SMALLINT` are not aliases. They keep their own range.
See "Integer type names" below. `CHAR(N)` is not fixed-width. It is a maximum,
like `VARCHAR(N)`.

In an HTTP table definition, the `OID` type is named `id`. In SQL, `id` is an
ordinary column name.

## Choosing a type

| Data | Use | Avoid |
| --- | --- | --- |
| Internal identifier | `OID DEFAULT (gen_id())` or `UUID` | `STRING` |
| UUID from another system | `UUID` | `STRING(36)`; UUID text is larger in rows and indexes |
| Money, exact quantities | `NUMERIC` | `FLOAT64` |
| Measurements, scores | `FLOAT64`, or `FLOAT32` to save space | |
| Counters, epoch milliseconds | `INT64` | |
| Point in time | `DATETIME` (UTC) | `STRING` dates |
| Calendar day | `DATE` | |
| Short text with a known bound | `STRING(N)` | Bare `STRING` when a bound exists |
| Enumerated status | `STRING(N)` plus `CHECK (status IN (...))` | |
| JSON document you query inside | `JSONB` | `STRING` |
| JSON text you must keep byte for byte | `STRING` with the text JSON functions | `JSONB`; it stores a canonical form |
| A JSON value you sort, range-scan, or enforce | A typed column beside the document | Expecting an index on `doc ->> 'k'` |
| Blob, image, archive | `BYTES` with `STORAGE EXTERNAL` | |
| Vector embedding | `BYTES(N) STORAGE PLAIN` plus a `CHECK` on `vector_dims(...)` | |
| List of tags | `ARRAY(STRING)` when you never filter by an index on it | `ARRAY` when you need an index |

The type list has no `INTERVAL` type. Every `DATETIME` is an instant in UTC.
CamusDB parses date and datetime text as UTC.

## Integer type names

| Declaration | Accepted range | `SHOW CREATE TABLE` shows |
| --- | --- | --- |
| `SMALLINT` | -32768 to 32767 | `SMALLINT` |
| `INT`, `INTEGER` | -2147483648 to 2147483647 | `INT` |
| `INT64`, `BIGINT` | The full 64-bit range | `INT64` |

- A write or a cast outside the declared range fails with `CADB0420` (HTTP 400,
  gRPC `OUT_OF_RANGE`). This covers `INSERT`, `UPDATE`, upserts, row APIs,
  defaults, the fill of an added column, and the elements of `ARRAY(SMALLINT)`
  and `ARRAY(INT)`.
- Every width uses `INT64` storage, arithmetic, indexes, and results. If
  `quantity SMALLINT` holds 32767, `quantity + 1` returns 32768, but a write of
  that value back to the column fails.
- `CREATE TABLE ... AS SELECT` makes `INT64` columns, even from a narrow source.
- `INT16` and `INT32` are not types.
- **Existing columns keep their old range.** Before this change, `INT` and
  `SMALLINT` were unbounded `INT64` aliases. Those columns still accept any
  `INT64` and show as `INT64`. Old stored views and `CHECK` bodies keep their
  `INT64` casts.
- `SERIAL` and `BIGSERIAL` are both `INT64`, unlike PostgreSQL `SERIAL`.
- A sequence on a `SMALLINT` or `INT` column keeps its own bounds. A generated
  value outside the column range fails the write with `CADB0420`.
- Use `INT64` or `BIGINT` when a value can pass the 32-bit range.

```sql
CREATE TABLE inventory (id BIGINT PRIMARY KEY, quantity SMALLINT, deliveries INT);
SELECT CAST(32768 AS SMALLINT);  -- CADB0420
```

## INT64

- The range is `-9223372036854775808` to `9223372036854775807`.
- A result outside the range fails with `CADB0420 IntegerValueOutOfRange`. It
  never wraps. The statement stores nothing.
- `+`, `-`, `*`, `/`, unary minus, `SUM`, integer literals, and conversions to
  `INT64` are checked. Use `NUMERIC '...'` for a wider exact value.
- `CAST(x AS INT64)`, `x::int64`, and `to_int64(x)` round a fraction half away
  from zero, for `FLOAT64`, `FLOAT32`, `NUMERIC`, and a `JSONB` number.
  `CAST(2.5 AS INT64)` is `3` and `CAST(-2.5 AS INT64)` is `-3`. PostgreSQL
  rounds a half to the nearest even integer.
- A cast to `SMALLINT` or `INT` rounds first, then checks the range.
- `CAST` of `NaN`, an infinity, or an out-of-range value fails with `CADB0420`.
- `LIMIT`, `OFFSET`, subscripts, and integer columns still refuse a float
  without an explicit `CAST`.
- `CAST('2.5' AS INT64)` fails with `CADB0400`. A string must spell an integer.
- An `INT64` column refuses a float value without a `CAST`.

## FLOAT64 and FLOAT32

- `'Infinity'`, `'-Infinity'`, and `'NaN'` are values. Write them as text, with
  `CAST`, or as a parameter.
- A finite result too large or too small for the type fails with
  `CADB0421 FloatValueOutOfRange`. It never becomes an infinity or a zero.
- A `FLOAT64` value written to a `FLOAT32` column fails when it is past the
  `FLOAT32` range or rounds to zero.
- The order is `-Infinity` < every number < `Infinity` < `NaN`.
  `ORDER BY v DESC` puts `NaN` first. `MAX(v)` is `NaN` when a row holds it.
- `NaN = NaN` is true. `GROUP BY` and `DISTINCT` give one group for `NaN`.
- `-0` equals `0`. A `UNIQUE` index refuses `-0` next to `0`. `WHERE v = 0`
  finds both. A covering index read returns `-0` as `0`.
- An index built before these rules holds old keys for `-0` and `NaN`. Rebuild
  it with `DROP INDEX` and `CREATE INDEX` if the column holds either value.
- A `NaN` or infinity cannot `CAST` to `NUMERIC` (`CADB0400`).

## DATE and DATETIME

- A `DATE` value written to a `DATETIME` column becomes midnight UTC. This
  applies to `INSERT`, `UPDATE`, and `DEFAULT`.
- A `DATETIME` value written to a `DATE` column is refused.
- A `DATETIME` literal can be a bare date: `'2026-03-15'`.
- A comparison parses a string into the `DATE` or `DATETIME` type. Text that
  does not parse fails with `CADB0400`.

## Comparing different types

Two values compare only when their types have a rule. These pairs have one:

- Two values of the same type.
- Two numbers of different types.
- `DATE` with `DATETIME`, or a `STRING` with either.
- `STRING` with `UUID`, `OID`, or `JSONB`.
- Two arrays with the same element type.

Any other pair fails with `CADB0400`. Examples: `INT64` with `STRING`, `BOOL`
with `INT64`, `BYTES` with `STRING`, and `NUMERIC` with `STRING`. Write a `CAST`.

- Known incompatible types fail before any row is read, even on an empty table.
  Types known only from a parameter value are checked at evaluation.
- A new `CHECK` with known incompatible types fails at definition time. A type
  error reached while an existing check runs rejects the write with `CADB0303`.
- A stored `CHECK` with a float-to-`INT64` cast uses the rounding rule for new
  writes only. Existing rows are not checked again.

## Errors before reading rows

A mistake whose types are known from literals, casts, columns, or operators
fails even on an empty table, under `LIMIT 0` or `WHERE FALSE`, or in an unused
branch:

- Unknown functions (`CADB0407`) and wrong argument counts (`CADB0400`).
- Incompatible operators and comparisons, and known invalid assignments
  (`CADB0401`). A table with a `BEFORE` trigger checks assignments after the
  trigger, because the trigger can replace the value.
- A `WHERE`, `HAVING`, `JOIN ON`, or `CHECK` condition that is not boolean or
  `NULL`. `WHERE 1` and `WHERE 'true'` fail with `CADB0400`. Write `WHERE TRUE`.
- A constant division by zero, such as `1 / 0`, fails with `CADB0400`. A proven
  unused branch, such as `IF(TRUE, 1, 1 / 0)`, is skipped. Stored view and
  `CHECK` definitions defer this error until use.

These checks cover writes, subqueries, CTAS, views, and new `CHECK`
definitions.

## JSONB

`JSONB` stores one JSON document, or SQL `NULL`. `JSON` is an alias, and
`SHOW CREATE TABLE` shows `JSONB`. Neither word is reserved.

Choose `JSONB` when:

- Queries read keys or test containment (`->`, `->>`, `@>`, `?`).
- You want a GIN index over the document.
- You want invalid JSON rejected at write time (`CADB0418 InvalidJsonText`).
- You need exact numbers. `JSONB` keeps all digits and the scale: `1.50`.

Choose `STRING` when:

- You must keep the input text, including whitespace, key order, and duplicates.
- The value is not always valid JSON.

The stored form is canonical:

- Keys are sorted: the shorter key first, then by bytes.
- The last value of a repeated key wins.
- Exponent notation becomes plain digits: `1e3` reads back as `1000`.
- JSON `null` and SQL `NULL` are different values.

Rules for a `JSONB` column:

- It cannot be in a primary key, a `UNIQUE` constraint, or an ordered index. It
  fails with `CADB0400`. It cannot be in a foreign key.
- `ARRAY(JSONB)` is not a type. An array of documents is one document.
- No expression index exists on `(doc ->> 'k')`. Copy that value to a typed
  column if you need order, uniqueness, or a foreign key.
- `STORAGE` applies as for `STRING`. The default is `EXTENDED`.
- Nesting is limited to 512 levels (`CADB0419 JsonNestingTooDeep`). The limit is
  fixed.
- Numbers are limited to 131,072 digits before the point and a scale of 16,383.
- `jsonb_agg` and `jsonb_object_agg` fail with `CADB0422 JsonbValueTooLarge`
  above 268,435,455 bytes.
- A stored cell that does not decode fails with `CADB0559 JsonbValueCorrupt`.
- A gRPC client built before `JSONB` support reads every cell as `NULL`, with no
  error. Update the client first.

The docs do not say if a `JSONB` column can be an `INCLUDE` column of an ordered
index. Test it before you rely on it.

GIN index rules are in [ddl-syntax.md](ddl-syntax.md#gin-indexes).

## NUMERIC

- Fixed precision 38 and scale 9. Range: `-99999999999999999999999999999.999999999`
  to `99999999999999999999999999999.999999999`.
- `NUMERIC(10, 2)` and `DECIMAL(10, 2)` are rejected. Enforce a scale with a
  `CHECK` if you need one.
- More than 9 fractional digits round half away from zero.
- Overflow fails with `CADB0417 NumericValueOutOfRange`.
- `NUMERIC` with `NUMERIC` or `INT64` returns `NUMERIC`. `NUMERIC` with a float
  returns `FLOAT64`.
- `SUM`, `AVG`, `MIN`, and `MAX` over `NUMERIC` return `NUMERIC`.
- Use `NUMERIC '0.1'` for a typed literal in an expression. A bare decimal
  literal written into a `NUMERIC` column keeps its exact text.
- `ARRAY(NUMERIC)` is not supported yet.
- `NUMERIC` can be a primary key or index column. Its index encoding preserves
  order.

## STRING and BYTES lengths

| Column | Maximum |
| --- | --- |
| `STRING(N)`, `CHAR(N)`, `VARCHAR(N)` | `N` Unicode code points |
| `STRING` | 2,621,440 code points |
| `BYTES(N)` | `N` bytes |
| `BYTES` | 10,485,760 bytes (10 MB) |

- `N` must be a positive integer.
- The bound is a maximum, not a fixed width.
- A longer value fails with `CADB0302 ValueTooLong`. CamusDB never truncates.
  This applies to `INSERT`, `UPDATE`, `CAST`, and a `DEFAULT`.
- A `NULL` is not checked against the bound.
- For an exact byte count, add a `CHECK`.
- An emoji such as `🙂` is one code point, so it fits in `CHAR(1)`. Combining
  marks count separately.
- `CHAR(N)` neither pads nor trims spaces. `'ab'` differs from `'ab '`, and
  `'abc '` is too long for `CHAR(3)`. PostgreSQL `CHAR` differs.

## Text order and valid Unicode

- Comparisons, sorting, `MIN`, `MAX`, and indexes use binary UTF-16 code-unit
  order. There is no locale collation and no `COLLATE` clause. Use `LOWER` for a
  case-insensitive comparison.
- A lone half of a UTF-16 surrogate pair is invalid text. A SQL string parameter
  or string-array element with one fails with `CADB0400` before execution, even
  when the parameter is unused. Typed row writes also refuse it and name the
  column.
- Stored replacement characters (U+FFFD) stay unchanged.
- A gRPC (Protobuf) client can replace a surrogate half with U+FFFD before it
  sends the value. The server cannot detect that change. Validate text in the
  application.

## ARRAY(T)

- `T` must be a scalar type. `ARRAY(ARRAY(INT64))` is rejected.
- An array cannot be a primary key column or an index key column. It can be
  an `INCLUDE` column of a covering index.
- An element can be `NULL`.
- Subscripts start at `1`: `tags[1]`. An index outside the array gives `NULL`.
- Use `cardinality`, `array_length`, `array_contains`, `= ANY(...)`, and
  `> ALL(...)` to read an array.
- An empty literal `ARRAY[]` adopts the column element type.
- In a `CHECK`, use `cardinality(tags)` for a length rule.
  `array_length(tags, 1)` returns `NULL` for an empty array, so the check passes.

## Literal formats

| Type | Literal | Example |
| --- | --- | --- |
| `INT64` | An integer | `42` |
| `NUMERIC` | A decimal or a typed literal | `NUMERIC '0.1'` |
| `FLOAT64`, `FLOAT32` | A decimal number, or quoted `NaN` or `Infinity` | `3.14`, `'NaN'` |
| `STRING` | Single or double quotes | `'hello'`, `"hello"` |
| `BOOL` | `true` or `false` | `true` |
| `OID` | A quoted 24-character hex string | `'507f1f77bcf86cd799439011'` |
| `UUID` | A quoted UUID, with hyphens or 32 hex digits | `'550e8400-e29b-41d4-a716-446655440000'` |
| `DATE` | A quoted `yyyy-MM-dd` | `'2026-03-15'` |
| `DATETIME` | A quoted ISO-8601 UTC string | `'2026-03-15T12:00:00Z'` |
| `BYTES` | Hex in `X'...'` (even digit count) | `X'DEADBEEF'` |
| `ARRAY(T)` | `ARRAY[...]` | `ARRAY[1, 2, 3]` |
| `JSONB` | A quoted document, or a typed literal | `'{"a": 1}'`, `JSONB '[1, 2]'` |

String literal rules:

- Double quotes make a string literal, not an identifier. Quote an identifier
  with backticks: `` `comment` ``.
- A plain string does not treat a backslash as an escape. `'C:\Users\data'`
  stores the backslashes.
- Escape a quote by repeating it: `'it''s ready'`.
- Use `E'...'` for control characters: `E'line1\nline2'`.
- `0xFF` is an integer, not bytes. Use `X'FF'`.
- `to_id('...')` and `str_id('...')` convert 24 lowercase hex characters to an
  `OID`.

## Defaults

| Default | Column type | Behavior |
| --- | --- | --- |
| A constant, such as `DEFAULT (0)` | Any matching type | Stored in the schema. |
| `DEFAULT (gen_id())` | `OID` | New ObjectId per row. Time-ordered. |
| `DEFAULT (gen_uuid_v4())` | `UUID`, `GUID` | Random UUID per row. |
| `DEFAULT (gen_uuid_v7())` | `UUID`, `GUID` | Time-ordered UUID per row. |
| `DEFAULT (nextval('seq'))` | `INT64` or a narrower integer | Next sequence value. A value outside the column range fails with `CADB0420`. |
| `DEFAULT (now())` | `DATETIME` | Current time. All rows of one statement get the same value. |

- A function default must be a bare call with no argument.
- A default whose type does not match the column is rejected, for example
  `DEFAULT (gen_id())` on a `UUID` column.
- A string default longer than the column bound fails with `CADB0302`.

## Reserved type keywords

These are reserved:

```text
oid object_id int int64 integer smallint string char varchar text bool boolean
serial bigserial numeric decimal float float32 float64 real date datetime
timestamp bytes blob uuid guid array
```

`BIGINT` and `PRECISION` stay usable as identifiers.

Only the exact word is reserved. `dates`, `internal`, and `blob_store` are
valid names.
