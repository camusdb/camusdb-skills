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
| `$1`, `?`, `:name` placeholders | `@name`. A prepared statement binds by position in first-occurrence order. |
| SQL `PREPARE p AS ...` / `EXECUTE p` | Not a SQL statement. Prepare through gRPC `BatchExecute`, REST `/prepare-sql-statement`, or a driver. |
| `x - -5` written as `x --5` | `--` starts a comment. Put a space: `x - -5`. |
| `0xFF_FF` | `0xFF` takes no digit separators. `65_535` is fine. |
| `'{a,b}'` array text | `ARRAY['a', 'b']`. |
| `ARRAY[1, 2.5]` | Cast to one type: `ARRAY[1.0, 2.5]` or `ARRAY[CAST(1 AS FLOAT64), 2.5]`. |

## Queries

| You might write | In CamusDB |
| --- | --- |
| `SELECT a b FROM t` (bare column alias) | `SELECT a AS b FROM t`. |
| `LIMIT 10, 20` (MySQL) | `LIMIT 20 OFFSET 10`. |
| `COUNT(DISTINCT x)` | Not supported. `SELECT COUNT(*) FROM (SELECT DISTINCT x FROM t) d`. |
| `SELECT DISTINCT a, COUNT(*) ... GROUP BY a` | Drop `DISTINCT`. A group is already distinct. |
| `SELECT name, COUNT(*) FROM t` (MySQL loose group) | Add `GROUP BY name`. |
| `SELECT 1 WHERE cond` | Add a `FROM` clause. Or `SELECT EXISTS (SELECT 1 FROM t WHERE cond)`. |
| `SELECT COUNT(*)` without `FROM` | `SELECT (SELECT COUNT(*) FROM t)`. |
| `FULL [OUTER] JOIN` | Not supported. |
| `NATURAL JOIN`, `JOIN ... USING (id)` | `JOIN b ON b.id = a.id`. |
| `a JOIN b ... RIGHT JOIN c` | Not supported when the left operand is a join. Put the preserved table first and use `LEFT JOIN`. |
| `x IN (SELECT a, b FROM ...)` | Not supported. Use `EXISTS` with both equalities. |
| Correlated `x IN (SELECT ... WHERE u.k = t.k)` | Not supported. Use a correlated `EXISTS`. |
| Correlated subquery inside `= ANY (...)` | Not supported. Use `EXISTS`. |
| `x NOT IN (SELECT nullable_col ...)` | Legal, but one `NULL` drops every non-match. Prefer `NOT EXISTS`. |
| `WITH cte AS (...) SELECT ...` | Not documented. Use a derived table or a view. |
| `WITH RECURSIVE` | Not supported. |
| `UNION`, `UNION ALL`, `INTERSECT`, `EXCEPT` | Not documented. Run separate queries, or redesign with joins. |
| `ROW_NUMBER() OVER (...)`, other window functions | Not documented. Use `ORDER BY ... LIMIT`, or a derived table with `GROUP BY`. |
| `SIMILAR TO`, `[[:alpha:]]` | Use `~` with .NET syntax, such as `\p{L}`. |
| `tags[1:2]` slice | Not supported. `tags[n]` reads one element (1-based). |
| `tags @> ARRAY['x']`, `<@` | Not supported. `'x' = ANY (tags)` or `array_contains(tags, 'x')`. |
| `ANY (tags) = x` (quantifier on the left) | `x = ANY (tags)`. |
| `table AS OF SYSTEM TIME` inside `BEGIN` (CockroachDB) | A plain historical read runs in autocommit only. It works as the source of `INSERT ... SELECT` or CTAS. |
| `FROM t@{FORCE_INDEX=idx}` (Spanner) | Same form: `FROM t@{FORCE_INDEX=idx}`. See `camusdb-query-performance`. |

## Writes

| You might write | In CamusDB |
| --- | --- |
| `UPDATE t SET x = 1` with no `WHERE` | Rejected. Add a predicate that matches every row. Watch the mutation limit. |
| `DELETE FROM t` to empty a table | Rejected. `TRUNCATE TABLE t`. |
| `UPDATE t SET ... FROM other`, `UPDATE a JOIN b` | Not documented. Use a subquery in `WHERE`, or several statements in a transaction. |
| `UPDATE ... ORDER BY ... LIMIT` (MySQL) | `LIMIT n` is accepted. `ORDER BY` in `UPDATE`/`DELETE` is not documented. |
| `INSERT ... ON CONFLICT DO UPDATE` | Not documented. Read and write in one transaction; see `camusdb-transactions`. |
| `INSERT ... ON DUPLICATE KEY UPDATE`, `REPLACE INTO`, `UPSERT`, `INSERT OR IGNORE` | Not documented. Same as above. |
| `INSERT INTO t2 SELECT * FROM t1` with different column order | Columns match by position. List columns on both sides. |
| `RETURNING COUNT(*)` | Rejected (`CADB0400`). `RETURNING` takes per-row expressions only. |
| `RETURNING nextval('s')` | Rejected (`CADB0547`). |
| Bulk `INSERT ... SELECT` of a million rows | Fails over 20,000 mutations (`CADB0506`). Batch with `WHERE` or `LIMIT` across transactions. |
| `TRUNCATE a, b` | One table per statement. |
| `TRUNCATE ... CASCADE` | Not supported. Truncate the child first or drop the foreign key. |
| `BEGIN; TRUNCATE t; COMMIT;` | Rejected (`CADB0538`). Run `TRUNCATE` outside a transaction. |
| `CREATE TABLE t AS SELECT a + 1 FROM s` | Alias it: `a + 1 AS a_next`. |
| `CREATE TABLE t AS SELECT NULL AS x` | `CAST(NULL AS INT64) AS x`. |
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
| `x::text`, `x::int`, `x::double` | Supported. `CAST(x AS double)` too. |
| `a + b::int` meaning `(a + b)::int` | `::` binds tighter. Write `(a + b)::int`. |
| `'2024-06-15 10:30:00'` in a date function | Add an offset: `'2024-06-15T10:30:00Z'`. |
| `ObjectId('...')` | `str_id('...')` or `to_id('...')`, 24 lowercase hex characters. |
| `gen_random_uuid()` | `gen_uuid_v4()`. |
| `uuid_generate_v7()` | `gen_uuid_v7()`. |
| `x'...'` vs `0x...` for bytes | `X'DEADBEEF'` is bytes. `0xFF` is an integer. |

## Functions

| You might write | In CamusDB |
| --- | --- |
| `a \|\| b` for text | Not documented. `concat(a, b)`. Note that `concat` skips `NULL` arguments. |
| `NULLIF(a, b)` | Not in the function catalog. `CASE WHEN a = b THEN NULL ELSE a END`. |
| `IFNULL`, `NVL`, `COALESCE` | All supported. |
| `GREATEST`, `LEAST`, `LPAD`, `SPLIT_PART`, `STRING_AGG`, `GROUP_CONCAT`, `ARRAY_AGG` | Not documented. |
| `char_length(x)` | `length(x)`. |
| `POSITION(a IN b)`, `STRPOS(b, a)` | Supported. |
| `btrim(x, chars)` | `trim(x, chars)`. |
| `EXTRACT(YEAR FROM d)`, `YEAR(d)` | `date_part('year', d)`. |
| `d + INTERVAL '1 day'`, `DATE_ADD(d, INTERVAL 1 DAY)` | `date_add(d, 1, 'day')`. Returns `DATETIME`. |
| `DATEDIFF(a, b)` (MySQL) | `date_diff(start, end, 'day')`. Mind the argument order. |
| `date_trunc('month', x)` | Same order. Returns `DATETIME` in UTC. |
| `UNIX_TIMESTAMP(x)`, `FROM_UNIXTIME(s)`, `to_timestamp(s)` | Supported. |
| `to_char(123, '999')` | Not supported. Only dates format with patterns. |
| `str_to_date(...)` compared with a `DATE` column | Returns `DATETIME`. Wrap it in `CAST(... AS date)`, or use `to_date(text, pattern)`. |
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
