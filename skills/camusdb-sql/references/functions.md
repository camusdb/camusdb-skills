# CamusDB scalar function catalog

Read this file before you call a function in CamusDB SQL. If a function is not
listed here or in the files below, the docs do not describe it. Do not use it.

- `jsonb_*` functions and operators: [jsonb.md](jsonb.md).
- Window functions and `generate_series`: [set-and-window.md](set-and-window.md).
- `INTERVAL` values and interval functions: "Intervals" below.

Source pages: https://camusdb.github.io/docs/functions, each `functions-*` page
linked from it, and https://camusdb.github.io/docs/data-types#intervals.

## General rules

- A function name is not case-sensitive. Functions nest freely.
- A function works anywhere a value works: projection, `WHERE`, `GROUP BY`,
  `HAVING`, `DEFAULT`, `CHECK`.
- Most functions return `NULL` when an argument is `NULL`. Exceptions are noted.
- An unknown function (`CADB0407`) or a wrong argument count (`CADB0400`) fails
  before any row is read, also on an empty table. Argument types known from
  literals, casts, or columns are checked then too. Parameters and some argument
  types are checked when the value arrives. See [expressions.md](expressions.md).
- Inline C# functions (`CREATE FUNCTION ... LANGUAGE csharp`) share this namespace.
  They are in the `camusdb-csharp-extensions` skill.
- Volatile functions fall into three groups:
  - Per call: `gen_id()`, `gen_uuid_v4()`, `gen_uuid_v7()`, `random()`,
    `gen_random_bytes(n)`, `clock_timestamp()`, `sysdate()`. Do not use them in a
    predicate that must be stable across a scan.
  - Per statement: `statement_timestamp()`.
  - Per transaction: `now()`, `current_timestamp()`, `transaction_timestamp()`,
    `current_date()`, `unix_timestamp()`, `unix_timestamp_ms()`.
  - `age(...)` is marked volatile. A `CHECK` cannot use it, and a query that calls
    it skips the result cache.
- A column `DEFAULT` may call a no-argument generator such as `gen_id()`,
  `gen_uuid_v7()`, or `now()`. A per-call generator runs once per inserted row.
  `DEFAULT now()` gives every row of one `INSERT` the same time.
- Session functions are not allowed in `DEFAULT` or `CHECK`.
- Bad input fails with `CADB0400` `InvalidInput` unless noted.

## Aggregates

| Function | Meaning |
| --- | --- |
| `COUNT(*)` | Counts every row. |
| `COUNT(col)` | Counts non-null values. |
| `SUM(col)` | Sum. `INT64` in, `INT64` out; `NUMERIC` in, `NUMERIC` out; `FLOAT64` in, `FLOAT64` out. An `INT64` total past the range fails with `CADB0420`. A float total past the range fails with `CADB0421`. |
| `AVG(col)` | `NUMERIC` for `NUMERIC` input, else `FLOAT64`. |
| `MIN(col)`, `MAX(col)` | Smallest or largest non-null value. |

`COUNT(DISTINCT col)` is not supported. An empty input makes `SUM` return
`NULL`; wrap it as `COALESCE(SUM(x), 0)`. `jsonb_agg` and `jsonb_object_agg` are in
[jsonb.md](jsonb.md). Add `OVER (...)` for the window form; see
[set-and-window.md](set-and-window.md).

## Comparison rule

Two values compare only when their types have a rule:

- Two values of the same type.
- Two numbers. `INT64` with `NUMERIC` compares exactly. A float compares as a double.
- A `DATE` with a `DATETIME`. The date reads as midnight UTC.
- A `STRING` with a `DATE` or `DATETIME`. The string is parsed. A bad string fails
  with `CADB0400`. A `DATE` takes only `yyyy-MM-dd`.
- A `STRING` with a `UUID`, `OID`, or `JSONB`. The string is parsed. A bad string
  equals no value, with no error.
- Two arrays with the same element type, or one that is `ARRAY[]` or all `NULL`.

Every other pair fails with `CADB0400`: `5 = '5'`, `0.0 = '0.0'`, `bool_col = 1`,
`bytes_col = 'a'`, `NUMERIC '1' = '1'`, `ARRAY[1] = ARRAY[1.0]`. Write a `CAST`.

- The rule covers `=`, `<>`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `IN`, `NOT IN`, simple
  `CASE`, `NULLIF`, `= ANY`, `array_contains`, `JOIN ... ON`, and `HAVING`.
- A new `CHECK` that compares such a known pair fails at definition. A type error
  while an existing `CHECK` runs rejects the row with `CADB0303`.
- `x IN (a, b)` evaluates both items, so `1 IN (1, 'a')` fails.
- A simple `CASE` stops at the first match, so `CASE 5 WHEN 5 THEN 1 WHEN 'a' THEN 2 END`
  returns 1.
- A `NULL` operand gives UNKNOWN before any type check: `NULL = 'a'` is `NULL`.
- Known incompatible types fail before any row is read, also on an empty table.
  Types known only from values, such as parameters, are checked when compared.

## Session

| Function | Returns | Meaning |
| --- | --- | --- |
| `current_database()` | `STRING` | Database of the statement. `NULL` for server-level statements. |
| `current_user()` | `STRING` | Authenticated user. `NULL` while authentication is off. |
| `current_role()` | `STRING` | Same value as `current_user()`. |
| `is_superuser()` | `BOOL` | Whether the caller is a superuser. `NULL` while authentication is off. |

A query with a session function bypasses the result cache.

## String

| Function | Returns | Meaning |
| --- | --- | --- |
| `length(text)` | `INT64` | Unicode code points; an emoji counts as one. For `BYTES`, byte count. |
| `octet_length(x)` | `INT64` | Byte count. For a string, UTF-8 bytes. |
| `bit_length(x)` | `INT64` | 8 × bytes of `BYTES`, or of UTF-8 text. |
| `lower(text)`, `upper(text)` | `STRING` | Case change, invariant rules. |
| `trim(x [, set])`, `ltrim(...)`, `rtrim(...)` | same as input | No set: strip white space (text) or `0x00` (bytes). With set: strip those code points or bytes. |
| `substring(x, start [, length])`, `substr(...)` | same as input | 1-based, in code points (text) or bytes. See below. |
| `replace(text, search, replacement)` | `STRING` | Replace every ordinal match. |
| `strpos(x, part)` | `INT64` | 1-based code-point position of `part`, or `0`. |
| `position(part IN x)` | `INT64` | Same as `strpos(x, part)`. `x` cannot start with `(`. |
| `contains(x, part)` | `BOOL` | Ordinal substring test. Both arguments must have the same type. |
| `starts_with(x, prefix)`, `ends_with(x, suffix)` | `BOOL` | Ordinal prefix or suffix test. |
| `concat(value, ...)` | `STRING` | Joins scalars by their text form. Skips `NULL`; `NULL` only if all are `NULL`. An array fails. |
| `like_escape(pattern, escape)` | `STRING` | A `LIKE` pattern with a custom escape, in backslash form. `ESCAPE` uses it. |

`substring` and `substr` follow PostgreSQL. A start below 1 uses up that part of
the length. Without a length, a start below 1 returns the whole value. A start
past the end returns an empty value. A negative length fails with `CADB0400`.

```sql
SELECT substr('abcde', 0, 2);      -- 'a'
SELECT substring('abcde', -1, 3);  -- 'a'
SELECT substring('x🙂y', 2, 1);    -- '🙂'
SELECT ltrim('🙂x', '😀');          -- '🙂x', the set counts code points
```

Text positions count code points, but sorting uses binary UTF-16 order. See
"Text and Unicode" in [expressions.md](expressions.md).

`a || b` also joins text, but a `NULL` operand gives `NULL`. See "Operators" below.

The text form of a value is the same in `CAST(x AS string)`, `to_string`, `concat`,
and `||`: a date is `2026-03-15`, bytes are `\xdeadbeef`, and a float is the shortest
text that reads back as the same value. A `datetime` has seven fractional digits. An
interval is ISO-8601 duration text, such as `P1Y2M3DT4H5M6.5S`.

## Operators

| Operator | Operands | Meaning |
| --- | --- | --- |
| `a \|\| b` | two strings, two bytes, or a string with a scalar | Concatenation. A `NULL` operand gives `NULL`. `1 \|\| 2` and an array operand fail. |
| `a & b`, `a \| b`, `a # b` | two `INT64`, or two `BYTES` of equal length | AND, OR, XOR. |
| `~a` | `INT64` or `BYTES` | Bitwise NOT. After an operand, `~` is the regex match. |
| `a << n`, `a >> n` | two `INT64` | Shifts. The count is modulo 64. `>>` keeps the sign. |
| `-a` | a number | Negation. The smallest `INT64` fails with `CADB0420`. |

- `a ^ b` is power, the same as `pow(a, b)`. It binds tighter than `*`. Use `#` for XOR.
- A float, `NUMERIC`, `BOOL`, or `STRING` operand of a bitwise operator fails.
- `||`, the bitwise operators, the regex operators, and the `jsonb` operators share
  one level, below `+` and `-`. So `6 | 1 & 3` is `(6 | 1) & 3`, and `1 << 1 + 1` is
  `1 << 2`. The full table is in [expressions.md](expressions.md).
- Prefix `~` binds as tightly as unary minus: `~a + 1` is `(~a) + 1`. PostgreSQL
  reads it as `~(a + 1)`. Add parentheses.

```sql
SELECT first_name || ' ' || last_name FROM users;
SELECT 'n=' || 5, X'01' || X'02';
SELECT id FROM items WHERE flags & 4 = 4;
```

## Math

Arguments are `INT64`, `FLOAT32`, `FLOAT64`, or `NUMERIC`.

| Function | Returns | Meaning |
| --- | --- | --- |
| `abs(v)` | same type | Absolute value. Fails on the minimum `INT64` with `CADB0420`. |
| `ceil(v)` / `ceiling(v)` | same type | Round up. |
| `floor(v)` | same type | Round down. |
| `round(v)` | same type | Nearest integer, half away from zero. |
| `round(v, scale)` | `NUMERIC` for `INT64` or `NUMERIC` input, `FLOAT64` for a float | Round to `scale` places. Negative scale rounds left of the point. `round(15, -1)` is `NUMERIC` 20. |
| `trunc(v [, scale])` | same type | Truncate toward zero. `INT64` input stays `INT64`. |
| `sqrt(v)` | `FLOAT64` | Square root. Negative input fails. |
| `pow(b, e)` / `power(b, e)` / `b ^ e` | `FLOAT64` | Power. Overflow or underflow fails with `CADB0421`. `pow(0, y)` for a negative `y` and a negative `b` with a non-integer `e` fail with `CADB0400`. |
| `ln(v)` | `FLOAT64` | Natural logarithm. |
| `log10(v)`, `log(v)` | `FLOAT64` | Base-10 logarithm. |
| `log(base, v)` | `FLOAT64` | Base first, as in PostgreSQL: `log(2, 8)` is 3. GoogleSQL uses the other order. |
| `exp(v)` | `FLOAT64` | e raised to `v`. Overflow or underflow to zero fails with `CADB0421`. |
| `mod(a, b)` and `a % b` | `INT64`, `NUMERIC`, or `FLOAT64` | Remainder with the dividend's sign. Zero divisor fails. |
| `sign(v)` | `INT64` | `-1`, `0`, or `1`. `sign(NaN)` fails. |
| `random()` | `FLOAT64` | Volatile, in `[0.0, 1.0)`. |

Logarithms and `exp` return `FLOAT64`, also for `NUMERIC` input. A logarithm
refuses zero and negative arguments, and a base of 1.

`INT64` arithmetic never wraps: a result past the range fails with `CADB0420`.
Float arithmetic that overflows or underflows from finite values fails with
`CADB0421`. `x / 0` fails with `CADB0400`. With constant operands, it fails before
any row is read. See "Float values" below.

### Trigonometry and angles

All return `FLOAT64`, also for `INT64`, `NUMERIC`, and `FLOAT32` input. A `NULL`
argument gives `NULL`.

| Function | Meaning |
| --- | --- |
| `sin(x)`, `cos(x)`, `tan(x)`, `cot(x)` | Functions of an angle in radians. |
| `asin(x)`, `acos(x)`, `atan(x)` | Inverse functions. Return radians. |
| `atan2(y, x)` | Angle of the vector `(x, y)` in radians, in the right quadrant. |
| `sind(x)`, `cosd(x)`, `tand(x)`, `cotd(x)` | Functions of an angle in degrees. |
| `asind(x)`, `acosd(x)`, `atand(x)`, `atan2d(y, x)` | Inverse functions. Return degrees. |
| `radians(degrees)`, `degrees(radians)` | Convert between units. |
| `pi()` | π as `FLOAT64`. |

```sql
SELECT sind(30), cosd(90), tand(45), atan2d(1, 1);   -- 0.5, 0, 1, 45
SELECT radians(180), degrees(pi());                  -- about 3.141592653589793, 180
```

- Prefer the degree forms for data in degrees. `sind(30)`, `cosd(90)`, and
  `tand(45)` are exact. The radian forms use the platform math library, so their
  last digits can differ across platforms.
- `NaN` propagates. An infinite angle, and an `asin` or `acos` input outside
  `[-1, 1]`, fail with `CADB0400`.
- A pole returns infinity, as in PostgreSQL: `cot(0)`, `tand(90)`, `cotd(0)`.
- A conversion that overflows or underflows a finite value fails with `CADB0421`.

## Float values

- `'Infinity'`, `'-Infinity'`, and `'NaN'` are values. Write them as text in a float
  column, with `CAST`, or as a parameter. `inf` also works. Text is case-insensitive.
- An operation on them gives the IEEE result: `CAST('Infinity' AS float64) + 1` is
  `Infinity`.
- The order is `-Infinity` < numbers < `Infinity` < `NaN`. `NaN = NaN` is true.
  `-0` equals `0`. `MAX(v)` is `NaN` when a row holds it.
- `CAST` of an infinity or `NaN` to `INT64` fails with `CADB0420`. To `NUMERIC`, it
  fails with `CADB0400`.
- A subnormal result is a value, not an underflow.
- A value past the `FLOAT32` range, or one that rounds to zero, fails on a narrowing
  write or cast.

## Date and time

Inputs: a `DATE`, a `DATETIME`, `'YYYY-MM-DD'`, or a timestamp string with `Z`
or an offset. A local timestamp string without an offset is rejected.

| Function | Returns | Meaning |
| --- | --- | --- |
| `now()`, `current_timestamp()`, `transaction_timestamp()` | `DATETIME` | Start of the transaction, UTC. Write the parentheses. |
| `current_date()` | `DATE` | UTC date of the start of the transaction. |
| `statement_timestamp()` | `DATETIME` | Start of the statement, UTC. |
| `clock_timestamp()`, `sysdate()` | `DATETIME` | The clock at each call, UTC. |
| `date_add(value, amount, unit)` | `DATETIME` | Add an `INT64` amount. A `DATE` becomes a `DATETIME`. |
| `date_add(value, interval)`, `date_sub(value, interval)` | `DATETIME` | Add or subtract an `INTERVAL`, with calendar months. |
| `date_diff(start, end, unit)` | `INT64` | Whole units from `start` to `end`, truncated toward zero. |
| `date_part(unit, value)` | `INT64` | UTC component, or a field of an interval. |
| `EXTRACT(unit FROM value)` | `INT64` | SQL-standard form of `date_part`. The unit may be a bare word. |
| `date_trunc(unit, value)` | `DATETIME` | Start of the unit, UTC. |
| `unix_timestamp([value])` | `INT64` | Unix seconds. With no argument, of the start of the transaction. |
| `unix_timestamp_ms([value])` | `INT64` | Unix milliseconds, rounded down. With no argument, of the start of the transaction. |
| `from_unixtime(seconds)` | `DATETIME` | Datetime from Unix seconds. A fraction rounds to the microsecond. |
| `to_timestamp(seconds)` | `DATETIME` | PostgreSQL spelling of `from_unixtime`. |
| `make_date(year, month, day)` | `DATE` | Integer parts. An invalid part fails. |
| `make_timestamp(year, month, day, hour, minute, second)` | `DATETIME` | UTC. `second` may have a fraction. |
| `makedate(year, day_of_year)` | `DATE` | MySQL. Day 1 is January 1. Past the year's end, it moves to the next year. |
| `last_day(value)` | `DATE` | Last day of the UTC month. |
| `year`, `month`, `day`, `dayofmonth`, `hour`, `minute`, `second`, `quarter`, `dayofyear` `(value)` | `INT64` | Same as `date_part`. |
| `dayofweek(value)` | `INT64` | 1 = Sunday to 7 = Saturday. |
| `weekday(value)` | `INT64` | 0 = Monday to 6 = Sunday. |
| `week(value [, mode])` | `INT64` | Mode 0 (default): weeks start on Sunday, 0-53. Mode 3: ISO week, 1-53. Other modes fail. |
| `dayname(value)`, `monthname(value)` | `STRING` | English names. |

`now()` has one value for every row and statement of a transaction. A retry is a new
transaction, so it gets a new `now()`. With `AS OF SYSTEM TIME`, `now()` is still the
start of the transaction. A query that calls a clock function skips the query result
cache. `WHERE col > now()` can use an index on `col`.

`make_date`, `make_timestamp`, `makedate`, and `last_day` are deterministic. The year
must be 1-9999. A `NULL` argument gives `NULL`.

`DATE(x)` is not a function. Write `CAST(x AS date)`.

```sql
SELECT date_add('2024-06-15', 1, 'day');            -- 2024-06-16T00:00:00Z
SELECT date_diff('2024-06-01', '2024-06-11', 'days'); -- 10
SELECT date_trunc('month', happened_at) FROM events;
SELECT EXTRACT(week FROM created), dayofweek(created) FROM events;
SELECT make_date(2024, 2, 29), last_day('2024-02-10');
```

### Date units

Every unit reads the UTC value. Plurals such as `days` work. The unit is a string,
or a bare word in `EXTRACT`. A unit with no meaning in a function fails.

| Unit | `date_part` / `EXTRACT` | `date_trunc` | `date_add` / `date_diff` |
| --- | --- | --- | --- |
| `year` | calendar year | January 1 | years |
| `quarter` | 1-4 | first day of the quarter | 3 months |
| `month` | 1-12 | first day of the month | months |
| `week` | ISO week, 1-53 | Monday 00:00 | 7 days |
| `day` | 1-31 | 00:00 | days |
| `hour`, `minute`, `second` | 0-23, 0-59, 0-59 | start of the unit | the unit |
| `millisecond` | fraction of the second, 0-999 | yes | yes |
| `microsecond`, `us` | fraction of the second, 0-999999 | yes | yes |
| `dow` | 0 = Sunday to 6 = Saturday | no | no |
| `isodow` | 1 = Monday to 7 = Sunday | no | no |
| `doy` | day of the year, 1-366 | no | no |
| `isoyear` | ISO year of the ISO week | no | no |
| `epoch` | whole seconds since 1970-01-01 UTC | no | no |
| `decade` | year / 10 | January 1 of a year ending in 0 | 10 years |
| `century` | 2000 is century 20, 2001 is century 21 | January 1 of a year ending in 01 | 100 years |
| `millennium` | 2000 is millennium 2, 2001 is millennium 3 | January 1 of a year ending in 001 | 1000 years |

A part is always `INT64`. PostgreSQL returns `numeric`, so two units differ: `epoch`
is whole seconds, and `millisecond` and `microsecond` are the fraction of the current
second only.

## Intervals

`INTERVAL` holds months, days, and microseconds. It is an expression type only. A
column, an array element, a CTAS column, and a materialized-view column cannot hold
it. A plain view can return one. To store one, cast it to `STRING`, and cast the
text back to `INTERVAL` when you read it.

```sql
SELECT * FROM orders WHERE created_at > now() - INTERVAL '7 days';
SELECT CAST('2024-01-31' AS DATE) + INTERVAL '1 month';   -- 2024-02-29T00:00:00Z
SELECT finished_at - started_at AS duration FROM jobs;
SELECT DATE_ADD(CAST('2024-01-31' AS DATE), INTERVAL 1 MONTH);
```

| Form | Example |
| --- | --- |
| PostgreSQL text | `INTERVAL '1 year 2 mons 3 days 04:05:06'` |
| ISO-8601 text | `INTERVAL 'P1DT2H'` |
| Single unit | `INTERVAL '3' HOUR`, `INTERVAL 3 HOUR` |
| Parameter with a unit | `INTERVAL @n DAY` |
| Cast from text | `CAST('1 day' AS INTERVAL)`, `'1 day'::INTERVAL` |

- Units: `microsecond`, `millisecond`, `second`, `minute`, `hour`, `day`, `week`,
  `month`, `quarter`, `year`, `decade`, `century`, `millennium`. Plurals and short
  forms such as `mons`, `hrs`, and `us` work, in any case.
- Text takes signed parts (`'-1 day +2 hours'`), `ago` (`'3 hours ago'`), and
  fractions. `'1.5 days'` is 1 day and 12 hours. `'1.5 months'` is 1 month and 15
  days. A fractional year rounds to whole months. A bare number in the text means
  seconds.
- With a trailing unit, the text must be a number: `INTERVAL '3' HOUR` works, and
  `INTERVAL '1 day' HOUR` fails.
- Only text casts to `INTERVAL`. Give a number a unit, or multiply it:
  `days_to_ship * INTERVAL '1 day'`.

| Expression | Result |
| --- | --- |
| `DATETIME ± INTERVAL`, `INTERVAL + DATETIME` | `DATETIME` |
| `DATE ± INTERVAL`, `INTERVAL + DATE` | `DATETIME`, from midnight UTC |
| `DATETIME - DATETIME`, `DATE - DATETIME`, `DATETIME - DATE` | `INTERVAL` with days and time, no months |
| `INTERVAL ± INTERVAL`, `-INTERVAL` | `INTERVAL` |
| `INTERVAL * number`, `number * INTERVAL`, `INTERVAL / number` | `INTERVAL` |
| Two intervals with `=`, `<>`, `<`, `<=`, `>`, `>=` | `BOOL` |

- Date arithmetic applies months, then days, then time. An invalid day clips to the
  last day of the month. Values are UTC, so an interval day is 24 hours.
- Scaling moves a month fraction into days at 30 days per month, and a day fraction
  into time: `INTERVAL '1 month' * 0.5` is 15 days.
- A comparison uses 30-day months and 24-hour days. `INTERVAL '1 month' =
  INTERVAL '30 days'` is true, also for `GROUP BY` and `DISTINCT`. Added to a date,
  the two can still give different dates.
- A `NULL` operand gives `NULL`. An interval plus a number, an interval times an
  interval, and an interval minus a date fail.
- Overflow fails with `CADB0400`. Months and days must each fit a signed 32-bit
  integer, microseconds a signed 64-bit integer, and a result timestamp years 1 to
  9999.
- Keep an indexed column outside the calculation. `created_at > now() - INTERVAL
  '7 days'` can use an index range scan. `created_at + INTERVAL '7 days' > now()`
  cannot. Check with `EXPLAIN`.
- The .NET, TypeScript, and Python drivers return an interval as ISO-8601 text,
  such as `P1Y2M3DT4H5M6.5S`. Zero is `PT0S`. Mixed signs stay: `P-1DT2H`.
- Not supported: `SUM` and `AVG` of intervals, a comparison with plain text (write
  `INTERVAL '1 day'`, not `'1 day'`), and MySQL compound units such as `HOUR_MINUTE`.

### Interval functions

| Function | Returns | Meaning |
| --- | --- | --- |
| `age(a, b)` | `INTERVAL` | Calendar difference `a - b`, in years, months, days, and time. |
| `age(a)` | `INTERVAL` | `age(current_date(), a)`, with the transaction's UTC date at midnight. |
| `make_interval(years, months, weeks, days, hours, mins, secs)` | `INTERVAL` | From positional parts. Omitted trailing parts are zero. |
| `justify_days(i)` | `INTERVAL` | Each 30 days become a month. Opposite month and day signs are adjusted. |
| `justify_hours(i)` | `INTERVAL` | Each 24 hours become a day. Opposite day and time signs are adjusted. |
| `justify_interval(i)` | `INTERVAL` | Both conversions, with signs adjusted across the parts. |
| `date_bin(stride, source, origin)` | `DATETIME` | Start of the bucket that holds `source`, aligned to `origin`. |

```sql
SELECT age(CAST('2001-04-10' AS DATE), CAST('1957-06-13' AS DATE));  -- P43Y9M27D
SELECT make_interval(0, 0, 0, 1, 2);                                 -- P1DT2H
SELECT justify_interval(INTERVAL '1 month -1 hour');                 -- P29DT23H
SELECT date_bin(INTERVAL '15 minutes',
  CAST('2024-06-15T15:44:17Z' AS DATETIME),
  CAST('2024-06-15T00:00:00Z' AS DATETIME));                         -- 2024-06-15T15:30:00Z
```

- `age` borrows days by the month length of the earlier date, as PostgreSQL does.
  Both forms are volatile in CamusDB: a `CHECK` cannot use them, and a query that
  calls them skips the result cache. PostgreSQL's two-argument form is immutable.
- `make_interval` takes zero to seven positional arguments. Named arguments are not
  supported. Every part except seconds must be `INT64`. Seconds can have a
  fraction, rounded to the microsecond. A part out of range fails with `CADB0400`.
- `date_bin` needs a positive stride with no month part, and an explicit origin. A
  value before the origin rounds down to the bucket before it.
- A `NULL` argument gives `NULL`.

### EXTRACT from an interval

`EXTRACT` and `date_part` read the stored parts and do not normalize them:
`EXTRACT(hour FROM INTERVAL '36 hours')` is 36, and
`date_part('day', INTERVAL '36 hours')` is 0.

- Years are the total months divided by 12. The month field is the remainder. Each
  field keeps the sign of its part.
- Results are `INT64`, not PostgreSQL's fractional `NUMERIC`. `second` is whole
  seconds. `millisecond` and `microsecond` are the fraction of the current second.
- `epoch` is whole seconds, rounded down, with 365.25 days per year and 30 days per
  remaining month.
- `week`, `dow`, `isodow`, `doy`, and `isoyear` are calendar positions. They do not
  work on an interval.

## Date patterns

| Function | Returns | Pattern language |
| --- | --- | --- |
| `to_char(temporal, pattern)` | `STRING` | PostgreSQL |
| `to_timestamp(text, pattern)` | `DATETIME` | PostgreSQL |
| `to_date(text, pattern)` | `DATE` | PostgreSQL |
| `date_format(temporal, pattern)` | `STRING` | MySQL |
| `str_to_date(text, pattern)` | `DATETIME` (always) | MySQL |

Rules: all values are UTC. Names are English only. The functions are
deterministic, so `CHECK`, `DEFAULT`, and view bodies can use them. Years run
1–9999. Fractions have six digits. A parse is strict: the whole text must
match, the pattern needs a year, and redundant fields (day name, quarter, week)
must agree with the date. A comparison of a `DATE` with a `DATETIME` reads the date
as midnight UTC. To get a `DATE`, cast the result of `str_to_date`, or use
`to_date(text, pattern)`.

PostgreSQL tokens (case-sensitive):

| Token | Meaning |
| --- | --- |
| `YYYY`, `YYY`, `YY`, `Y` | Year, 4/3/2/1 digits. Short years parse to the year nearest 2020. |
| `IYYY`, `IW`, `ID` | ISO year, ISO week, ISO weekday (1 = Monday). |
| `MM` | Month `01`–`12`. |
| `Mon`, `MON`, `mon` | `Mar`, `MAR`, `mar`. |
| `Month`, `MONTH`, `month` | Full month name, blank-padded to 9. |
| `DD`, `DDD` | Day of month; day of year. |
| `D` | Weekday, 1 = Sunday. |
| `Dy`, `DY`, `dy` / `Day`, `DAY`, `day` | Short / full day name (full padded to 9). |
| `WW` | Week of year; week 1 is January 1–7. |
| `Q` | Quarter. |
| `HH`, `HH12`, `HH24` | Hour 01–12; hour 00–23. |
| `MI`, `SS` | Minute; second. |
| `MS`, `US` | Milliseconds; microseconds. |
| `AM`, `PM`, `am`, `pm` | Meridian. Needs a 12-hour field in a parse. |
| `TZ`, `tz`, `OF` | `UTC`, `utc`, `+00`. |
| `FM` prefix | No padding for the next field. |
| `"text"` | Literal text. Quote letters, as in `'YYYY-MM-DD"T"HH24:MI:SS'`. |

Not supported: number formatting such as `to_char(123, '999')`, `TH`, `FX`,
`TM`, `CC`, `J`, `RM`, `A.M.`/`P.M.`, `BC`/`AD`, `TZH`/`TZM`, `SSSS`.

MySQL specifiers:

| Specifier | Meaning |
| --- | --- |
| `%Y`, `%y` | Year 4 digits; 2 digits (00–69 → 2000s, 70–99 → 1900s). |
| `%m`, `%c` | Month `01`–`12`; `1`–`12`. |
| `%d`, `%e` | Day `01`–`31`; `1`–`31`. |
| `%H`, `%h`, `%I` | Hour `00`–`23`; `01`–`12`. |
| `%i`, `%s`, `%S` | Minute; second. |
| `%f` | Microseconds. |
| `%p` | `AM` or `PM`. |
| `%M`, `%b` | Month name; 3-letter month. |
| `%W`, `%a` | Day name; 3-letter day. |
| `%j` | Day of year. |
| `%u`, `%v` | Week (Monday start, `WEEK(x, 1)`); ISO week. |
| `%%` | A literal `%`. |

Any other specifier is an error. Not supported: `%D`, `%k`, `%l`, `%r`, `%T`,
`%U`, `%V`, `%w`, `%X`, `%x`.

```sql
SELECT to_char(created, 'FMDay, DD Mon YYYY HH12:MI AM') FROM events;
SELECT date_format(created, '%W %M %e, %Y') FROM events;
SELECT * FROM events WHERE day = CAST(str_to_date('June 17, 2024', '%M %e, %Y') AS date);
```

## Conversion

| Function | Returns | Meaning |
| --- | --- | --- |
| `to_string(v)` | `STRING` | From any scalar, by its text form. An `INTERVAL` becomes ISO-8601 duration text. An array fails. |
| `to_int64(v)` | `INT64` | Same as `CAST(v AS int64)`. From `INT64`, `FLOAT64`, `FLOAT32`, `NUMERIC`, `BOOL`, or integer text. A float or `NUMERIC` rounds half away from zero; bool → 1/0. |
| `to_float64(v)` | `FLOAT64` | From numbers, bool, numeric text. |
| `to_float32(v)` | `FLOAT32` | From numbers or numeric text. |
| `to_bool(v)` | `BOOL` | From bool, or `true`/`false` text in any case. |
| `to_date(v)` | `DATE` | Same as `CAST(v AS date)`. |
| `to_datetime(v)` | `DATETIME` | From date, datetime, or ISO-8601 text. |
| `to_bytes(v)` | `BYTES` | From bytes, or `0x`-prefixed hex text. |
| `to_id(v)` / `str_id(v)` | `OID` | From an OID, or 24 lowercase hex characters. |

`CAST` targets: `string`/`char`/`varchar`/`text`, `int64`/`bigint`,
`int`/`integer` (an `INT64` value checked against the signed 32-bit range),
`smallint` (checked against the signed 16-bit range), `numeric`/`decimal`,
`float`/`float64`/`double`/`double precision`, `float32`/`real`,
`bool`/`boolean`, `oid`/`id`/`object_id`, `uuid`/`guid`, `date`,
`datetime`/`timestamp`, `bytes`/`blob`, `jsonb`/`json`, and `interval` (from text
only; see "Intervals"). `x::type` accepts the same names.

### Cast to INT64

`CAST(x AS int64)`, `x::int64`, and `to_int64(x)` use one rule. A number with a
fraction rounds half away from zero: `2.5` gives `3`, and `-2.5` gives `-3`.

- A narrow cast (`smallint`, `int`) rounds first, then checks the range:
  `CAST(32768 AS SMALLINT)` fails with `CADB0420`.
- PostgreSQL rounds a float half to even, so its result for `2.5` is `2`.
- A string must spell an integer. `CAST('2.5' AS int64)` fails with `CADB0400`.
- `NaN`, an infinity, and a result past the range fail with `CADB0420`.
- There is no implicit conversion. An `INT64` column, an integer function argument,
  a subscript, a bitwise operator, and a `LIMIT` or `OFFSET` parameter refuse a float.

## Null handling

| Function | Meaning |
| --- | --- |
| `coalesce(v, ...)` | First non-null argument. |
| `ifnull(v, fallback)` | Two-argument `coalesce`. |
| `nvl(v, fallback)` | Alias of `ifnull`. |
| `nullif(v, other)` | `NULL` when `v = other` is true, else `v`. The result has the type of `v`. |
| `if(cond, a, b)` | `a` when `cond` is `TRUE`; `b` when it is `FALSE` or `NULL`. |

```sql
SELECT id, total / NULLIF(cnt, 0) AS ratio FROM counts;
SELECT IF(quantity > 0, 'in stock', 'sold out') AS availability FROM inventory;
```

`IF` is the same as `CASE WHEN cond THEN a ELSE b END`. It evaluates only the selected
result. A non-boolean condition fails. `NULLIF` evaluates both arguments and follows
the `=` type rule.

`COALESCE`, `IFNULL`, and `NVL` evaluate from left to right and stop at the first
non-`NULL` value:

```sql
SELECT COALESCE(1, 1 / 0);               -- 1; the fallback does not run
SELECT COALESCE(NULL, 2);                -- 2
SELECT COALESCE(1, nextval('s'));        -- does not advance s
```

- The stop does not skip a known type error: `COALESCE(1, upper('a'))` fails.
- An uncorrelated subquery, an aggregate, or a window result can be computed before
  the row expression. An unused fallback does not guard its errors:
  `COALESCE(1, (SELECT id FROM orders))` can fail on many rows.
- A correlated subquery's value error is raised only when the value is used.
- A proven unused fallback or branch is skipped, also for a constant division by
  zero: `IF(TRUE, 1, 1 / 0)` returns 1. A row-dependent condition does not hide a
  constant division by zero. See [expressions.md](expressions.md).
- `nextval` after a non-`NULL` literal or parameter that is proven returned draws
  nothing. When the statement cannot prove that, it can draw a value and leave a gap.

### Result types

`CASE`, `COALESCE`, `IFNULL`, `NVL`, and `IF` return one type. Every value converts
to it. A bad pair fails with `CADB0400`, also in a branch that never runs.

| Inputs | Result type |
| --- | --- |
| A `NULL` input, or an omitted `ELSE` | The type of the other inputs. |
| One type | That type. |
| Two numbers | The type `+` gives: `INT64` with `FLOAT64` is `FLOAT64`; `INT64` with `NUMERIC` is `NUMERIC`. |
| `DATE` with `DATETIME` | `DATETIME`. The date becomes its midnight. |
| A quoted string literal with `DATE` or `DATETIME` | That temporal type. The literal is parsed at bind. |
| Two arrays | An array, when the element types agree or one is `ARRAY[]`. |
| Any other pair | `CADB0400`. |

- `COALESCE(1, 'a')` and `CASE WHEN c THEN 1 ELSE '2' END` fail. A quoted literal does
  not become a number. Write `CAST('2' AS INT64)`.
- `COALESCE(date_col, '2026-01-01')` is a `DATE`. Only a literal converts: a `STRING`
  column with a `DATE` column fails.
- A built-in call with known argument types, or an extension function with a declared
  C# return type, gives its result type even when unused. So `COALESCE(1, upper('a'))`
  fails, and `COALESCE(1, sqrt(4.0))` is always `FLOAT64`.
- A parameter, a call with unknown argument types, and `json_value` are checked when
  the value arrives. A branch that does not run is not checked then.

## Object id and UUID

| Function | Returns | Meaning |
| --- | --- | --- |
| `gen_id()` | `OID` | New ObjectId. Volatile. |
| `to_id(v)`, `str_id(v)` | `OID` | Parse 24 lowercase hex characters. |
| `gen_uuid_v4()` | `UUID` | Random UUID. Volatile. |
| `gen_uuid_v7()` | `UUID` | Time-ordered UUID. Volatile. |

## JSON (text in a STRING)

These functions read JSON text. For a `jsonb` column, read [jsonb.md](jsonb.md). A
`jsonb` argument here is read as its text.

| Function | Returns | Meaning |
| --- | --- | --- |
| `json_valid(json)` | `BOOL` | `false` for invalid JSON or `NULL`. |
| `json_type(json)` | `STRING` | `object`, `array`, `string`, `number`, `boolean`, `null`. |
| `json_extract(json, path)` | `STRING` | JSON text at `path`. |
| `json_value(json, path)` | scalar | `STRING`, `INT64`, `FLOAT64`, `BOOL`, or `NULL`. Objects and arrays give `NULL`. |
| `json_array_length(json [, path])` | `INT64` | Array length, or `NULL` if not an array. |
| `json_contains(value, candidate)` | `BOOL` | Structural containment. |

Paths: `$`, `.name` (letters, digits, `_`), `[0]` (0-based). No quoted names,
wildcards, recursive descent, filters, or negative indexes. Invalid JSON
usually gives `NULL`, not an error.

## Regex

| Function | Returns | Meaning |
| --- | --- | --- |
| `regexp_like(text, pattern [, flags])` | `BOOL` | Match test. |
| `regexp_match(text, pattern [, flags])` | `ARRAY<STRING>` | First match: groups, or the whole match. `NULL` if none. |
| `regexp_replace(text, pattern, repl [, flags])` | `STRING` | Replaces the first match; flag `g` replaces all. `\1`…`\9` and `\&` are back references; `$` is literal. |
| `regexp_count(text, pattern [, start [, flags]])` | `INT64` | Non-overlapping matches. |
| `regexp_instr(text, pattern [, start, N, endoption, flags, subexpr])` | `INT64` | 1-based position, or `0`. |
| `regexp_substr(text, pattern [, start, N, flags, subexpr])` | `STRING` | Matched text, or `NULL`. |
| `regexp_split_to_array(text, pattern [, flags])` | `ARRAY<STRING>` | Split at each match. |

Flags: `i`, `c`, `m`, `n`, `s`, `x`, `g`. Unknown flags fail. The engine is
.NET; use `\p{L}`, not `[[:alpha:]]`. `regexp_matches` and
`regexp_split_to_table` are not supported.

A start argument and a returned position count Unicode code points from 1. The match
itself works on UTF-16 units: `regexp_count('🙂', '.')` is 2, and
`regexp_like('🙂', '^.$')` is false, unlike PostgreSQL. A result that holds half a
surrogate pair, or a position inside a pair, fails with `CADB0400`. Name the
character in the pattern when you can.

Write a pattern in a plain string with single backslashes. A plain string keeps
each backslash as typed, so `'\d'` reaches the regex engine as `\d`. Do not
double backslashes as in C# or Java source. Do not use `E'...'`: `E'\d+'`
stores `d+`.

```sql
SELECT regexp_match('invoice-2026-0007', 'invoice-(\d{4})-(\d+)');   -- ["2026", "0007"]
SELECT regexp_replace('2026-07-13', '(\d{4})-(\d{2})-(\d{2})', '\3/\2/\1');
SELECT regexp_replace('aabbcc', 'b', 'X', 'g');                     -- aaXXcc
```

## Bytes

| Function | Returns | Meaning |
| --- | --- | --- |
| `encode(bytes, format)` | `STRING` | `format` is `hex`, `base64`, or `escape`. |
| `decode(text, format)` | `BYTES` | Reverse of `encode`. |
| `hex(x)` / `unhex(text)` | `STRING` / `BYTES` | MySQL hex. `unhex` fails on bad or odd-length input. |
| `to_base64(x)` / `from_base64(text)` | `STRING` / `BYTES` | MySQL base64, no line breaks. |
| `convert_to(text, 'UTF8')` / `convert_from(bytes, 'UTF8')` | `BYTES` / `STRING` | UTF-8 only. |
| `get_byte(b, n)` / `set_byte(b, n, v)` | `INT64` / `BYTES` | 0-based byte access. |
| `get_bit(b, n)` / `set_bit(b, n, v)` | `INT64` / `BYTES` | 0-based bit access. |
| `bit_count(b)` | `INT64` | Number of 1 bits. |
| `gen_random_bytes(n)` | `BYTES` | `n` from 1 to 1024. Volatile. Not allowed as `DEFAULT`. |

`length`, `substring`, `strpos`, `position`, `trim`, `ltrim`, `rtrim`,
`starts_with`, `ends_with`, and `contains` also accept `BYTES`.

## Hash

| Function | Returns | Meaning |
| --- | --- | --- |
| `md5(x)`, `sha1(x)`, `sha256(x)`, `sha512(x)` | `STRING` | Lowercase hex digest of text (UTF-8) or bytes. |
| `digest(x, algorithm)` | `BYTES` | Raw digest. `md5`, `sha1`, `sha256`, `sha384`, `sha512`. |
| `hmac(x, key, algorithm)` | `BYTES` | HMAC with the same algorithms. |
| `crc32(x)` | `INT64` | CRC-32 checksum. |

Hash a number with a cast first: `md5(x::text)`. These are not password hashes.

## Array

| Function | Returns | Meaning |
| --- | --- | --- |
| `cardinality(a)` | `INT64` | Element count; empty array gives `0`. |
| `array_length(a, 1)` | `INT64` | Empty array gives `NULL`; any other dimension gives `NULL`. |
| `array_contains(a, v)` | `BOOL` | Membership with `IN` null semantics. A value with no comparison rule fails with `CADB0400`. |

## Sequence

| Function | Meaning |
| --- | --- |
| `nextval('s')` | Advance and return the next value. |
| `currval('s')` | Last value this transaction drew from `s`. |
| `lastval()` | Last value this transaction drew from any sequence. |
| `setval('s', n [, is_called])` | Move the counter. Only as the whole projection of a `SELECT` without `FROM`. |

A sequence call in a nested `SELECT` without `FROM` fails with `CADB0547`.
`COALESCE(1, nextval('s'))` does not advance `s`. See "Null handling" above.

See the `camusdb-schema-design` skill for sequences and identity columns.

## Vector

`vector_dims(bytes)` returns `octet_length / 4`. `l2_distance(a, b)` and
`cosine_distance(a, b)` sort ascending. `inner_product(a, b)` sorts descending.
See https://camusdb.github.io/docs/vector-search.
