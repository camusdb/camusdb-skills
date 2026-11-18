# Porting SQL to CamusDB

Read this file when you translate SQL from PostgreSQL, MySQL, CockroachDB, or
Spanner. Each row says what you might write, and what to write in CamusDB.
"Not documented" means the CamusDB docs describe no such feature. Do not emit it.

## Lexical and parameters

| You might write | In CamusDB |
| --- | --- |
| `"order"` as an identifier (PostgreSQL) | `` `order` ``. Double quotes make a string. |
| `'O\'Brien'` (MySQL) | `'O''Brien'`, or `E'O\'Brien'`. A plain string keeps `\` as typed. |
| `'line1\nline2'` expecting a newline | `E'line1\nline2'`. |
| `'^\\d+$'` (regex with doubled backslashes) | `'^\d+$'`. Single backslashes in a plain string. |
| `$1`, `?`, `:name` placeholders | `@name`. A prepared statement binds by position in first-occurrence order. `?` is a `jsonb` operator. |
| SQL `PREPARE p AS ...` / `EXECUTE p` | Not a SQL statement. Prepare through gRPC `BatchExecute`, REST `/prepare-sql-statement`, or a driver. |
| `x - -5` written as `x --5` | `--` starts a comment. Put a space: `x - -5`. |
| `0xFF_FF` | `0xFF` takes no digit separators. `65_535` is fine. |
| A script with several statements in one request | One statement per request. Split the script. |
| A raw tab or line break inside `'...'` | Refused. Write `E'a\tb'` or `E'line\nnext'`. |
| Copied SQL with a non-breaking space or a byte-order mark | `CADB0406`. Replace it with a normal space. |
| `$$ ... $$` dollar quotes for a string | Only for an inline C# body. See `camusdb-csharp-extensions`. |
| `'{a,b}'` array text | `ARRAY['a', 'b']`. A `jsonb` path or key list does accept `'{a,b}'`. |
| `ARRAY[1, 2.5]` | Cast to one type: `ARRAY[1.0, 2.5]` or `ARRAY[CAST(1 AS FLOAT64), 2.5]`. |

## Queries

| You might write | In CamusDB |
| --- | --- |
| `SELECT a b FROM t` (bare column alias) | `SELECT a AS b FROM t`. |
| `LIMIT 10, 20` (MySQL) | `LIMIT 20 OFFSET 10`. |
| `COUNT(DISTINCT x)` | Not supported. `SELECT COUNT(*) FROM (SELECT DISTINCT x FROM t) d`. |
| `SELECT DISTINCT a, COUNT(*) ... GROUP BY a` | Drop `DISTINCT`. A group is already distinct. |
| `SELECT name, COUNT(*) FROM t` (MySQL loose group) | Add `GROUP BY name`. |
| `SELECT 1 WHERE cond` | Supported. `FALSE` or `NULL` returns no row. `cond` must be boolean. |
| `WHERE 1`, `WHERE 'true'`, `HAVING 1` (MySQL truthy values) | `CADB0400`. Write a boolean, such as `WHERE TRUE`. |
| `SELECT COUNT(*) ... ORDER BY id` (global aggregate) | `CADB0400`. Order by an alias, an aggregate, or a position, or drop the `ORDER BY`. |
| `LIMIT -1`, `OFFSET -1` | `CADB0400`. Counts are nonnegative. |
| Unaliased `SELECT upper(name) FROM t` read as `upper` or `?column?` | The name is the zero-based position, such as `0`. Alias it. |
| `SELECT COUNT(*)` without `FROM` | `SELECT (SELECT COUNT(*) FROM t)`. |
| `FULL [OUTER] JOIN` | Not supported. |
| `NATURAL JOIN`, `JOIN ... USING (id)` | `JOIN b ON b.id = a.id`. |
| `a JOIN b ... RIGHT JOIN c` | Not supported when the left operand is a join. Put the preserved table first and use `LEFT JOIN`. |
| `x IN (SELECT a, b FROM ...)` | Not supported. Use `EXISTS` with both equalities. |
| Correlated `x IN (SELECT ... WHERE u.k = t.k)`, `= ANY (SELECT ...)` | Supported in a non-grouped select list, `SELECT ... WHERE`, `UPDATE ... SET`/`WHERE`, and `DELETE ... WHERE`. |
| `x NOT IN (SELECT nullable_col ...)` | Legal, but one `NULL` drops every non-match. Prefer `NOT EXISTS`. |
| `x IN (y + 1, CAST(z AS INT64))` | An expression item is a syntax error. Write `x = y + 1 OR x = CAST(z AS INT64)`. A column name alone is fine. |
| `a = b = c` | `CADB0406`. Write `a = b AND b = c`. |
| `name LIKE 'a\_%'`, `LIKE ... ESCAPE '#'` | Supported, as in PostgreSQL. `_` matches one code point. See [expressions.md](expressions.md). |
| `COLLATE "C"`, a locale collation | Not supported. Strings sort in binary UTF-16 order. Use `lower` to ignore case. |
| `WHERE int_col = '5'` (MySQL implicit conversion) | Fails with `CADB0400`. Write `int_col = 5` or `CAST(s AS INT64) = int_col`. |
| `WHERE text_col = 5`, `bool_col = 1` | Fails with `CADB0400`. Cast one side. |
| `CASE WHEN c THEN 1 ELSE '2' END` (PostgreSQL) | Fails with `CADB0400`. A quoted literal does not become a number. Cast it. |
| Correlated subquery in the select list | Supported when the select list has no grouping, aggregate, or window call. |
| Correlated subquery in `HAVING`, `JOIN ON`, or `ON CONFLICT DO UPDATE` | `CADB0533`. Move it to `WHERE`, or join a derived table. |
| `ORDER BY (SELECT ...)`, `GROUP BY (SELECT ...)` | Refused (`CADB0400`). |
| `ORDER BY x NULLS FIRST`, `NULLS LAST` | Not supported. `NULL` sorts first ascending and last descending. PostgreSQL does the opposite by default. |
| `WITH cte AS (...) SELECT ...` | Supported, also with `MATERIALIZED` and `NOT MATERIALIZED`. A CTE read twice is computed once. See [ctes-and-lateral.md](ctes-and-lateral.md). |
| `WITH d AS (DELETE ... RETURNING ...)` (writable CTE) | Not supported. Run the write as its own statement. |
| `WITH c AS (VALUES ...)` | Write `WITH c AS (SELECT * FROM (VALUES ...) AS v)`. |
| `WITH RECURSIVE` | Supported: `seed UNION [ALL] step`. Add a stop condition. The step and row limits fail with `CADB0560`. |
| `SEARCH`, `CYCLE` clauses, `CREATE RECURSIVE VIEW` | Not supported. `UNION` stops a cycle when whole rows repeat. Otherwise add an explicit stop condition, such as a depth bound. A view body can use `WITH RECURSIVE`. |
| Mutually recursive CTEs | Not supported. |
| Recursive seed `SELECT NULL` or a bare parameter | Becomes `STRING`. Cast the seed to the type you need. |
| `UNION`, `UNION ALL`, `INTERSECT`, `EXCEPT` | Supported, with `ALL`. See [set-and-window.md](set-and-window.md). |
| `(q1 UNION q2) ORDER BY a + 1` | An expression fails. Use `SELECT * FROM (q1 UNION q2) AS s ORDER BY s.a + 1`. |
| `UNION CORRESPONDING BY` | Not supported. |
| `ROW_NUMBER() OVER (...)`, other window functions | Supported. See [set-and-window.md](set-and-window.md). |
| `SUM(x) FILTER (WHERE ...)` | Syntax error. Write `SUM(CASE WHEN ... THEN x END)`. |
| `OVER w ... WINDOW w AS (...)` | Not supported. Write the specification in each `OVER`. |
| `GROUPS` frames, `EXCLUDE`, `RANGE n PRECEDING` | Not supported. Use `ROWS`. |
| `CROSS JOIN LATERAL (...)`, `LEFT JOIN LATERAL (...) ON true` | Supported. `RIGHT JOIN LATERAL` is refused. See [ctes-and-lateral.md](ctes-and-lateral.md). |
| `FROM t, generate_series(1, t.n)`, `FROM t, jsonb_each(t.doc)` | Supported. `LATERAL` is implicit for a table function. |
| `CROSS APPLY`, `OUTER APPLY` | Not supported. Write `CROSS JOIN LATERAL (...) AS x` or `LEFT JOIN LATERAL (...) AS x ON true`. |
| `VALUES (1), (2)` as a statement | Not supported. Write `SELECT * FROM (VALUES (1), (2)) AS v`. |
| `(VALUES ...)` in `FROM` | Supported. The alias is required. Columns are `column1`, `column2`, ... unless you list names: `AS v (id, label)`. |
| `FROM (SELECT ...) AS d (a, b)` | Supported. The list renames columns by position. |
| `x IN (SELECT * FROM one_col)` | Supported when `*` expands to exactly one column. |
| `SELECT generate_series(1, 3)` | `CADB0533`. Write `SELECT * FROM generate_series(1, 3)`. |
| `generate_series` over dates or timestamps | Not supported. Arguments are integers. Use `date_add(d, i, 'day')` or `d + i * INTERVAL '1 day'` with an integer series. |
| `unnest(a)`, `WITH ORDINALITY` | Not supported. |
| `FOR SHARE`, `FOR KEY SHARE` | `CADB0533`. |
| `FOR UPDATE` with a join, `DISTINCT`, `GROUP BY`, or a view | `CADB0400`. See `camusdb-transactions`. |
| `SIMILAR TO`, `[[:alpha:]]` | Use `~` with .NET syntax, such as `\p{L}`. |
| `tags[1:2]` slice | Not supported. `tags[n]` reads one element (1-based). |
| `tags @> ARRAY['x']`, `<@` on an array | Not supported. `'x' = ANY (tags)` or `array_contains(tags, 'x')`. These operators work on `jsonb`. |
| `ANY (tags) = x` (quantifier on the left) | `x = ANY (tags)`. |
| `table AS OF SYSTEM TIME` inside `BEGIN` (CockroachDB) | A plain historical read runs in autocommit only. It works as the source of `INSERT ... SELECT` or CTAS. |
| `FROM t@{FORCE_INDEX=idx}` (Spanner) | Same form: `FROM t@{FORCE_INDEX=idx}`. See `camusdb-query-performance`. |

## Writes

| You might write | In CamusDB |
| --- | --- |
| `UPDATE t SET x = 1` with no `WHERE` | Rejected. Add a predicate that matches every row. Watch the mutation limit. |
| `DELETE FROM t` to empty a table | Rejected. `TRUNCATE TABLE t`. |
| `UPDATE t SET ... FROM other` (PostgreSQL) | Supported. A target row that matches several source rows fails with `CADB0400`; PostgreSQL picks one. Aggregate the source first. |
| `UPDATE a JOIN b SET ...` (MySQL) | Write `UPDATE a SET ... FROM b WHERE b.k = a.k`. |
| `DELETE FROM t USING u WHERE ...` (PostgreSQL) | Supported. A multiply matched row is deleted once. |
| `DELETE t FROM t JOIN u ...` (MySQL) | Write `DELETE FROM t USING u WHERE u.k = t.k`. |
| `UPDATE ... FROM ... LIMIT`, `RETURNING *` in a joined write | Not supported. List target columns in `RETURNING`. |
| A table or column named `using` or `lateral` | Both words are reserved. Quote them with backticks. |
| `UPDATE ... ORDER BY ... LIMIT` (MySQL) | `LIMIT n` is accepted. `ORDER BY` and `OFFSET` in `UPDATE`/`DELETE` are not supported. |
| `UPDATE t SET v = 1, v = 2` | Fails with `CADB0403`. |
| `INSERT ... ON CONFLICT DO UPDATE` | Supported. It must name a target. See [statements.md](statements.md). |
| `UPSERT INTO` (CockroachDB) | Supported, on the primary key. |
| `INSERT ... ON DUPLICATE KEY UPDATE c = VALUES(c)` (MySQL) | Not supported. `ON CONFLICT (key) DO UPDATE SET c = excluded.c`. |
| `INSERT OR IGNORE`, `INSERT IGNORE` | `ON CONFLICT DO NOTHING`. |
| `REPLACE INTO` | Not documented. `UPSERT INTO` is close, but an unlisted column keeps its stored value. |
| `MERGE` | Not supported. |
| `INSERT INTO t AS x ... ON CONFLICT` | No alias on the target. Use the table name and `excluded`. |
| `ON CONFLICT ON CONSTRAINT my_pk` | Name the primary key `<table>_pkey`, or give its columns. |
| `SAVEPOINT` around a failed insert | Not available. Any failed statement ends an explicit transaction. Use `ON CONFLICT`. |
| `RETURNING old.x`, `RETURNING new.x` | Not supported. `UPDATE` returns the new row only. |
| `INSERT INTO t2 SELECT * FROM t1` with different column order | Columns match by position. List columns on both sides. |
| `RETURNING COUNT(*)` | Rejected (`CADB0400`). `RETURNING` takes per-row expressions only. |
| `RETURNING nextval('s')` | Rejected (`CADB0547`). |
| Bulk `INSERT ... SELECT` of a million rows | Fails over 20,000 mutations (`CADB0506`). Batch with `WHERE` or `LIMIT` across transactions. |
| `TRUNCATE a, b` | One table per statement. |
| `TRUNCATE ... CASCADE` | Not supported. Truncate the child first or drop the foreign key. |
| `BEGIN; TRUNCATE t; COMMIT;` | Rejected (`CADB0538`). Run `TRUNCATE` outside a transaction. |
| `CREATE TABLE t AS SELECT a + 1 FROM s` | Alias it: `a + 1 AS a_next`. |
| `CREATE TABLE t AS SELECT NULL AS x` | `CAST(NULL AS INT64) AS x`. A typed scalar subquery such as `(SELECT MAX(a) FROM s) AS x` also works. |
| `CREATE TABLE t AS SELECT * FROM a JOIN b ...` | List and alias each column. |
| Expecting CTAS to copy the key and indexes | It copies the shape only and adds `id OID` as the key. |

## Views

| You might write | In CamusDB |
| --- | --- |
| `CREATE VIEW v AS SELECT a + 1 FROM t` | Alias every expression. |
| `CREATE VIEW v AS SELECT *, extra FROM t` | Not allowed. List the columns. |
| `CREATE OR REPLACE VIEW` that removes or renames a column | `CADB0529`. Drop and create the view again. |
| `INSERT`/`UPDATE`/`DELETE` through a view | Not implemented. Every view is read-only (`CADB0525`). |
| `INSTEAD OF` trigger, rule | Not supported. |
| `WITH (security_invoker = true)` | Not supported. Views run as the owner. |
| `CREATE TEMP VIEW` | Not supported. |
| A view body with `WITH` | Supported. Each CTE reference is stored as a derived table and runs separately. |
| View across two databases | Not supported. |
| `DROP TABLE t CASCADE` | No `CASCADE`. Drop dependent views first. |
| `REFRESH MATERIALIZED VIEW CONCURRENTLY mv` | `REFRESH MATERIALIZED VIEW mv`. It never blocks readers. `CONCURRENTLY` fails (`CADB0533`). |
| `TRUNCATE mv` | `REFRESH MATERIALIZED VIEW mv WITH NO DATA`. |
| Expecting a view in `SHOW TABLES` or `\dt` | `SHOW VIEWS`, `SHOW MATERIALIZED VIEWS`. |

## Databases and inspection

| You might write | In CamusDB |
| --- | --- |
| Connect to a database that does not exist | `CREATE DATABASE db` first. Nothing is created implicitly (`CADB0010`). |
| `USE db;` sent through a driver or HTTP | `USE` is a `camus-cli` shell command. Set the database in the connection string or request. |
| `CREATE SCHEMA`, `information_schema` queries | Not documented. `information_schema` is a reserved name. Use `SHOW` statements. |
| `\d t`, `SHOW CREATE TABLE` (MySQL) | `DESCRIBE t`, `SHOW COLUMNS FROM t`, `SHOW CREATE TABLE t`. |
| `ALTER DATABASE a RENAME TO b` | Supported. `RENAME DATABASE a TO b` also works. |
| `CREATE TABLE ... COMMENT = 'x'` (MySQL) | `COMMENT 'x'` with no `=`. |
| `COMMENT ON CONSTRAINT`, comment on a primary-key index | Not supported. |
| `COMMENT ON COLUMN email IS ...` | Qualify it: `COMMENT ON COLUMN users.email IS ...`. |

## Types and casts

| You might write | In CamusDB |
| --- | --- |
| `NUMERIC(10, 2)`, `DECIMAL(10, 2)` | `NUMERIC` (fixed precision 38, scale 9). |
| `x::text`, `x::int`, `x::double` | Supported. `CAST(x AS double)` too. `x::int` checks the 32-bit range. |
| `BIGINT`, `DOUBLE PRECISION` | Supported aliases of `INT64` and `FLOAT64`. `INT16` and `INT32` are not. |
| `INT` or `SMALLINT` expecting a 64-bit range (old CamusDB) | New columns and casts check 32-bit and 16-bit ranges (`CADB0420`). Use `BIGINT` for wider values. |
| `SERIAL` expecting 32 bits (PostgreSQL) | `SERIAL` and `BIGSERIAL` are both `INT64`. |
| `CHAR(n)` expecting blank padding | No padding and no trim. `'ab'` differs from `'ab '`. |
| `CAST(2.5 AS INT64)` expecting `2` (PostgreSQL float) | Gives `3`. A float rounds half away from zero. |
| `CAST('2.5' AS INT64)` (MySQL) | Fails with `CADB0400`. A string must spell an integer. |
| `9223372036854775807 + 1` expecting a wrap | Fails with `CADB0420`. Write a wider number as `NUMERIC '...'`. |
| `1e308 * 10` expecting `Infinity` | Fails with `CADB0421`. `CAST('Infinity' AS float64)` is a value. |
| `NaN <> NaN` (IEEE) | `NaN = NaN` is true, as in PostgreSQL. `NaN` sorts above every number. |
| `x #-1` meaning XOR | `#-` is the `jsonb` path delete. Write `x # -1`. |
| `a ^ b` (PostgreSQL power) | Supported, with PostgreSQL precedence. Returns `FLOAT64`. |
| `a ^ b` (MySQL XOR) | Write `a # b`. |
| `doc['key']`, `jsonpath`, `jsonb_path_query`, `@?`, `@@` | Not supported. Write `doc -> 'key'`. See [jsonb.md](jsonb.md). |
| `jsonb_agg(x ORDER BY y)` | Not supported. |
| `row_to_json`, `jsonb_to_record`, `jsonb_populate_record` | Not supported. |
| A `json` column that keeps its input text | `json` is an alias of `jsonb`. The text is canonical. |
| `a + b::int` meaning `(a + b)::int` | `::` binds tighter. Write `(a + b)::int`. |
| `'2024-06-15 10:30:00'` in a date function | Add an offset: `'2024-06-15T10:30:00Z'`. |
| `TIMESTAMP '2024-06-15 10:30:00'` | The space is refused. Write `TIMESTAMP '2024-06-15T10:30:00Z'`. `DATE '2024-06-15'` works. |
| An `INTERVAL` column | Not supported. `INTERVAL` is an expression type only. Store `CAST(i AS STRING)`, and cast the text back. |
| `WHERE i > '1 day'` for an interval | Plain text does not compare with an interval. Write `INTERVAL '1 day'`. |
| `SUM(i)`, `AVG(i)` over intervals | Not supported. |
| `ObjectId('...')` | `str_id('...')` or `to_id('...')`, 24 lowercase hex characters. |
| `gen_random_uuid()` | `gen_uuid_v4()`. |
| `uuid_generate_v7()` | `gen_uuid_v7()`. |
| `x'...'` vs `0x...` for bytes | `X'DEADBEEF'` is bytes. `0xFF` is an integer. |

## Functions

| You might write | In CamusDB |
| --- | --- |
| `a \|\| b` for text (PostgreSQL) | Supported. A `NULL` operand gives `NULL`. `concat(a, b)` skips `NULL`. Two non-strings such as `1 \|\| 2` fail. |
| `a \|\| b` as logical OR (MySQL) | `\|\|` is concatenation. Write `a OR b`. |
| `NULLIF(a, b)` | Supported. The result has the type of `a`. |
| `IF(c, a, b)` (MySQL) | Supported. It evaluates only the selected result. |
| `IFNULL`, `NVL`, `COALESCE` | All supported. They stop at the first non-`NULL` value. All arguments need a common type, even unused: `COALESCE(1, 'a')` fails. |
| `GREATEST`, `LEAST`, `LPAD`, `SPLIT_PART`, `STRING_AGG`, `GROUP_CONCAT`, `ARRAY_AGG` | Not documented. |
| `char_length(x)` | `length(x)`. It counts code points, as in PostgreSQL. |
| `POSITION(a IN b)`, `STRPOS(b, a)`, `substr(x, s, n)` | Supported. A start below 1 follows PostgreSQL. |
| `ln`, `log`, `log10`, `exp` | Supported. `log(base, x)` takes the base first, as in PostgreSQL. GoogleSQL `LOG(x, base)` differs. |
| Regex `.` matching one emoji | It matches one UTF-16 unit, so an emoji counts as 2. Name the character in the pattern. |
| `btrim(x, chars)` | `trim(x, chars)`. |
| `EXTRACT(YEAR FROM d)`, `YEAR(d)` | Supported. Both are `INT64`. PostgreSQL returns `numeric`, so `epoch` and `millisecond` differ. |
| `EXTRACT(dow FROM d)`, `DAYOFWEEK(d)`, `WEEK(d, 3)` | Supported. See [functions.md](functions.md) for units and modes. |
| `DATE(x)` | `CAST(x AS date)`. `date` is a reserved word. |
| `CURRENT_TIMESTAMP`, `CURRENT_DATE` with no parentheses | Write `current_timestamp()`, `current_date()`. |
| `CURDATE()` (MySQL) | `current_date()`. It reads the start of the transaction. |
| `now()` expecting the time of each row (MySQL `SYSDATE()`) | `now()` is the start of the transaction. Use `clock_timestamp()` or `sysdate()`. |
| `make_date`, `make_timestamp`, `makedate`, `last_day` | Supported. A negative year (PostgreSQL BC) fails. |
| `d + INTERVAL '1 day'`, `now() - INTERVAL '7 days'` | Supported. A `DATE` or `DATETIME` plus an interval returns `DATETIME`. See [functions.md](functions.md). |
| `DATE_ADD(d, INTERVAL 1 DAY)`, `DATE_SUB(d, INTERVAL 1 DAY)` (MySQL) | Supported. `date_add(d, 1, 'day')` also works. Returns `DATETIME`. |
| `INTERVAL '1:30' HOUR_MINUTE` (MySQL compound unit) | Not supported. Write `INTERVAL '1 hour 30 minutes'`. |
| `ts1 - ts2` | Returns an `INTERVAL` with days and time, no months. Use `age(ts1, ts2)` for years and months. |
| `age`, `make_interval`, `justify_days`, `justify_hours`, `justify_interval`, `date_bin` | Supported. `age` is volatile, so a `CHECK` cannot use it. `make_interval` takes no named arguments. `date_bin` needs an origin. |
| `EXTRACT(epoch FROM interval)` | Supported. Returns whole seconds as `INT64`. |
| `sin`, `cos`, `atan2`, `sind`, `cosd`, `radians`, `degrees`, `pi()` | Supported. Return `FLOAT64`. |
| `DATEDIFF(a, b)` (MySQL) | `date_diff(start, end, 'day')`. Mind the argument order. |
| `date_trunc('month', x)` | Same order. Returns `DATETIME` in UTC. |
| `UNIX_TIMESTAMP(x)`, `FROM_UNIXTIME(s)`, `to_timestamp(s)` | Supported. |
| `to_char(123, '999')` | Not supported. Only dates format with patterns. |
| `str_to_date(...)` compared with a `DATE` column | Returns `DATETIME`. The date compares as midnight UTC. To get a `DATE`, wrap it in `CAST(... AS date)`, or use `to_date(text, pattern)`. |
| Unknown MySQL `%` specifier | Error, not printed text. |
| `sha256(x)` expecting `bytea` | Returns hex text. Use `digest(x, 'sha256')` for bytes. |
| `digest(x, 'sha224')` | Not supported. |
| `md5(123)` | Cast first: `md5(123::text)`. |
| `regexp_matches`, `regexp_split_to_table` | `regexp_match`, `regexp_split_to_array`. |
| `regexp_replace(s, p, r)` expecting all matches (MySQL) | Replaces the first match. Add flag `'g'`. |
| `regexp_replace(s, p, '$1')` | Use `'\1'`. A `$` is literal. |
| `json_extract(j, '$.a.*')`, `'$."key"'` | Not supported. Paths are `$`, `.name`, `[n]`. |
| `random()` in a `DEFAULT` or stable predicate | Volatile. Use generators such as `gen_id()` in defaults. |
| `current_user()` in a `DEFAULT` or `CHECK` | Rejected. Pass it in the `INSERT` instead. |
