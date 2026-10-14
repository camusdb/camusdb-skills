# CamusDB scalar function catalog

Read this file before you call a function in CamusDB SQL. If a function is not
listed here, the docs do not describe it. Do not use it.

Source pages: https://camusdb.github.io/docs/functions and each
`functions-*` page linked from it.

## General rules

- A function name is not case-sensitive. Functions nest freely.
- A function works anywhere a value works: projection, `WHERE`, `GROUP BY`,
  `HAVING`, `DEFAULT`, `CHECK`.
- Most functions return `NULL` when an argument is `NULL`. Exceptions are noted.
- CamusDB checks the argument count and types at execution, not at parse.
- Volatile functions: `gen_id()`, `gen_uuid_v4()`, `gen_uuid_v7()`,
  `current_timestamp()`, `now()`, `current_date()`, `unix_timestamp()`,
  `random()`. Also `gen_random_bytes(n)`. Do not use them in a predicate that
  must be stable across a scan.
- A column `DEFAULT` may call a no-argument generator such as `gen_id()`,
  `gen_uuid_v7()`, or `now()`. It runs once per inserted row.
- Session functions are not allowed in `DEFAULT` or `CHECK`.
- Bad input fails with `CADB0400` `InvalidInput` unless noted.

## Aggregates

| Function | Meaning |
| --- | --- |
| `COUNT(*)` | Counts every row. |
| `COUNT(col)` | Counts non-null values. |
| `SUM(col)` | Sum. `INT64` in, `INT64` out; `NUMERIC` in, `NUMERIC` out; `FLOAT64` in, `FLOAT64` out. |
| `AVG(col)` | `NUMERIC` for `NUMERIC` input, else `FLOAT64`. |
| `MIN(col)`, `MAX(col)` | Smallest or largest non-null value. |

`COUNT(DISTINCT col)` is not supported. An empty input makes `SUM` return
`NULL`; wrap it as `COALESCE(SUM(x), 0)`.

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
| `length(text)` | `INT64` | Character count. For `BYTES`, byte count. |
| `octet_length(x)` | `INT64` | Byte count. For a string, UTF-8 bytes. |
| `bit_length(x)` | `INT64` | 8 × bytes of `BYTES`, or of UTF-8 text. |
| `lower(text)`, `upper(text)` | `STRING` | Case change, invariant rules. |
| `trim(x [, set])`, `ltrim(...)`, `rtrim(...)` | same as input | No set: strip white space (text) or `0x00` (bytes). With set: strip those characters or bytes. |
| `substring(x, start [, length])` | same as input | 1-based. `start < 1` and negative `length` are invalid. |
| `replace(text, search, replacement)` | `STRING` | Replace every ordinal match. |
| `strpos(x, part)` | `INT64` | 1-based position of `part`, or `0`. |
| `position(part IN x)` | `INT64` | Same as `strpos(x, part)`. `x` cannot start with `(`. |
| `contains(x, part)` | `BOOL` | Ordinal substring test. Both arguments must have the same type. |
| `starts_with(x, prefix)`, `ends_with(x, suffix)` | `BOOL` | Ordinal prefix or suffix test. |
| `concat(value, ...)` | `STRING` | Joins `STRING`, `OID`, `UUID`, `INT64`, `FLOAT64`, `BOOL`. Skips `NULL`; `NULL` only if all are `NULL`. |

## Math

Arguments are `INT64`, `FLOAT64`, or `NUMERIC`.

| Function | Returns | Meaning |
| --- | --- | --- |
| `abs(v)` | same type | Absolute value. Fails on the minimum `INT64`. |
| `ceil(v)` / `ceiling(v)` | same type | Round up. |
| `floor(v)` | same type | Round down. |
| `round(v)` | same type | Nearest integer, half away from zero. |
| `round(v, scale)` | `FLOAT64` or `NUMERIC` | Round to `scale` places. Negative scale rounds left of the point. |
| `trunc(v [, scale])` | same / `FLOAT64` or `NUMERIC` | Truncate toward zero. |
| `sqrt(v)` | `FLOAT64` | Square root. Negative input fails. |
| `pow(b, e)` / `power(b, e)` | `FLOAT64` | Power. |
| `mod(a, b)` and `a % b` | `INT64`, `NUMERIC`, or `FLOAT64` | Remainder with the dividend's sign. Zero divisor fails. |
| `sign(v)` | `INT64` | `-1`, `0`, or `1`. |
| `random()` | `FLOAT64` | Volatile, in `[0.0, 1.0)`. |

## Date and time

Inputs: a `DATE`, a `DATETIME`, `'YYYY-MM-DD'`, or a timestamp string with `Z`
or an offset. A local timestamp string without an offset is rejected.
Units: `year`, `month`, `day`, `hour`, `minute`, `second`, `millisecond`, and
plurals such as `days`.

| Function | Returns | Meaning |
| --- | --- | --- |
| `current_timestamp()` / `now()` | `DATETIME` | Current instant, UTC. |
| `current_date()` | `DATE` | Current date, UTC. |
| `date_add(value, amount, unit)` | `DATETIME` | Add an `INT64` amount. A `DATE` becomes a `DATETIME`. |
| `date_diff(start, end, unit)` | `INT64` | Whole units from `start` to `end`. |
| `date_part(unit, value)` | `INT64` | UTC component. |
| `date_trunc(unit, value)` | `DATETIME` | Start of the unit, UTC. |
| `unix_timestamp()` | `INT64` | Current Unix seconds. |
| `unix_timestamp(value)` | `INT64` | Unix seconds of a date or time. |
| `from_unixtime(seconds)` | `DATETIME` | Datetime from Unix seconds. |
| `to_timestamp(seconds)` | `DATETIME` | PostgreSQL spelling of `from_unixtime`. |

```sql
SELECT date_add('2024-06-15', 1, 'day');            -- 2024-06-16T00:00:00Z
SELECT date_diff('2024-06-01', '2024-06-11', 'days'); -- 10
SELECT date_trunc('month', happened_at) FROM events;
```

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
must agree with the date. CamusDB does not compare `DATE` with `DATETIME`; cast
the result of `str_to_date` to `DATE`, or use `to_date(text, pattern)`.

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
| `to_string(v)` | `STRING` | From string, OID, UUID, INT64, FLOAT64, NUMERIC, BOOL. |
| `to_int64(v)` | `INT64` | Float truncates toward zero; NUMERIC rounds half away from zero; bool → 1/0. |
| `to_float64(v)` | `FLOAT64` | From numbers, bool, numeric text. |
| `to_float32(v)` | `FLOAT32` | From numbers or numeric text. |
| `to_bool(v)` | `BOOL` | From bool, or `true`/`false` text in any case. |
| `to_date(v)` | `DATE` | Same as `CAST(v AS date)`. |
| `to_datetime(v)` | `DATETIME` | From date, datetime, or ISO-8601 text. |
| `to_bytes(v)` | `BYTES` | From bytes, or `0x`-prefixed hex text. |
| `to_id(v)` / `str_id(v)` | `OID` | From an OID, or 24 lowercase hex characters. |

`CAST` targets: `string`/`char`/`varchar`/`text`, `int`/`int64`/`integer`/`smallint`,
`numeric`/`decimal`, `float`/`float64`/`double`, `float32`/`real`,
`bool`/`boolean`, `oid`/`id`/`object_id`, `uuid`/`guid`, `date`,
`datetime`/`timestamp`, `bytes`/`blob`. `x::type` accepts the same names.

## Null handling

| Function | Meaning |
| --- | --- |
| `coalesce(v, ...)` | First non-null argument. |
| `ifnull(v, fallback)` | Two-argument `coalesce`. |
| `nvl(v, fallback)` | Alias of `ifnull`. |

Numeric arguments widen (`FLOAT64` > `FLOAT32` > `INT64`; `NUMERIC` with a
float gives `FLOAT64`). Mixing `STRING` with a non-string fails.

## Object id and UUID

| Function | Returns | Meaning |
| --- | --- | --- |
| `gen_id()` | `OID` | New ObjectId. Volatile. |
| `to_id(v)`, `str_id(v)` | `OID` | Parse 24 lowercase hex characters. |
| `gen_uuid_v4()` | `UUID` | Random UUID. Volatile. |
| `gen_uuid_v7()` | `UUID` | Time-ordered UUID. Volatile. |

## JSON (text in a STRING)

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
| `array_contains(a, v)` | `BOOL` | Membership with `IN` null semantics. |

## Sequence

| Function | Meaning |
| --- | --- |
| `nextval('s')` | Advance and return the next value. |
| `currval('s')` | Last value this transaction drew from `s`. |
| `lastval()` | Last value this transaction drew from any sequence. |
| `setval('s', n [, is_called])` | Move the counter. Only as the whole projection of a `SELECT` without `FROM`. |

See the `camusdb-schema-design` skill for sequences and identity columns.

## Vector

`vector_dims(bytes)` returns `octet_length / 4`. `l2_distance(a, b)` and
`cosine_distance(a, b)` sort ascending. `inner_product(a, b)` sorts descending.
See https://camusdb.github.io/docs/vector-search.
