# Schema review checklist

Read this file in review mode. Walk every item for every table, index, and
`ALTER` statement. Each item has an ID, a severity, a detection rule, and a
fix. Items marked **[KRS]** apply only under key-range sharding. When the
routing mode is unknown, report them and say so. A cluster fixes its routing
mode when it is created. A plan that relies on the other mode needs a new
cluster and a dump and reimport.

## Severities

| Severity | Meaning |
| --- | --- |
| `ERROR` | CamusDB rejects the statement, or a write will fail. |
| `HIGH` | A hotspot, a contention point, or a data-integrity gap. |
| `MEDIUM` | Extra write cost, extra I/O, or a weak type choice. |
| `LOW` | Documentation, naming, or style. |

## Report format

Use this shape:

```text
Routing mode: hash | key-range sharding | unknown (KRS items reported)

| ID  | Severity | Location               | Problem                         | Fix |
| --- | -------- | ---------------------- | ------------------------------- | --- |
| A3  | ERROR    | orders.sql:12 idx_name | DESC on STRING column `name`    | Use ASC, or ... |
| B1  | HIGH     | events.id              | SERIAL key, high insert rate    | id UUID ... DEFAULT (gen_uuid_v4()) |

Passed: A1, A2, A4, ...
```

Give each fix as CamusDB SQL. Quote the exact error code when an item is an
`ERROR`.

## A. Statements CamusDB rejects (ERROR)

| ID | Check | Detection | Fix |
| --- | --- | --- | --- |
| A1 | Table has a primary key | `CREATE TABLE` with no `PRIMARY KEY` | Add one. Otherwise `CADB0400`. |
| A2 | No precision on `NUMERIC` | `NUMERIC(p, s)` or `DECIMAL(p, s)` | Use `NUMERIC`. Add a `CHECK` for range rules. |
| A3 | `DESC` only on fixed-width types | `DESC` on a `STRING` or `BYTES` key or index column | Use `ASC`. Fails with `InvalidInput`. |
| A4 | Arrays used correctly | `ARRAY` in a primary key or index key; `ARRAY(ARRAY(...))`; `ARRAY(NUMERIC)`; `ARRAY(JSONB)` | Move to a child table, or use `INCLUDE`. An array of documents is one `JSONB` document. |
| A5 | Supported foreign-key actions | `CASCADE`, `SET NULL`, `SET DEFAULT`, `MATCH FULL`, `MATCH PARTIAL`, `DEFERRABLE`, `INITIALLY DEFERRED` | Use `NO ACTION` or `RESTRICT`. Otherwise `CADB0533`. |
| A6 | No foreign-key cycle | Table A references B and B references A, or a longer loop | Remove one edge. Otherwise `CADB0416`. Self-reference is fine. |
| A7 | Valid foreign-key target | Target is not exactly the parent primary key or a unique index; types differ; target is a view or in another database | Fix the target. Otherwise `CADB0415`. |
| A8 | TTL and foreign keys apart | A TTL table is a foreign-key parent, in either order | Drop TTL or the foreign key. Otherwise `CADB0533`. |
| A9 | `STORAGE` on variable-length types only | `STORAGE` on a type other than `STRING`, `BYTES`, `JSONB`, `ARRAY` | Remove it. Otherwise `CADB0414`. |
| A10 | Valid `CHECK` | Subquery, aggregate, `now()`, `gen_id()`, `gen_uuid_v4()`, `gen_uuid_v7()`, unknown column; a named column-level check | Rewrite as a deterministic one-row predicate. Name it at table level. |
| A11 | Valid `INCLUDE` | `INCLUDE` on a primary key; a column in both key and `INCLUDE`; `ASC`/`DESC` in `INCLUDE` | Fix the list. |
| A12 | Index size limits | More than 32 key plus `INCLUDE` columns; `INCLUDE` payload above 4 KiB | Trim the index. |
| A13 | No foreign syntax | `STORING`, hash-sharded index, `INTERVAL`, `ttl_expire_after`, a five-field cron, an expression in `ttl_expiration_expression`, `CYCLE` on a sequence, negative `INCREMENT` | Use the CamusDB form in [ddl-syntax.md](ddl-syntax.md). |
| A14 | Identifiers quoted correctly | Identifiers in double quotes | Use backticks. Double quotes make a string. |
| A15 | Documented DDL forms only | A bare `DROP INDEX name`; `CONSTRAINT x UNIQUE (a, b)`; inline `INDEX name (...)`; a column type change | Use `DROP INDEX name ON t` or `DROP INDEX t.name`, `UNIQUE KEY`, `KEY`. For a type change, add a new column, copy in batches, then drop the old one. |
| A16 | Valid defaults | A function default with arguments; a type mismatch such as `gen_id()` on `UUID`; a string default longer than the column bound | Fix the default. |
| A17 | `nextval` placement | `nextval` in `UPDATE ... SET`, a filter, a view body, or a `CHECK` | Use it only in `INSERT ... VALUES`, defaults, or `SELECT` without `FROM`. Otherwise `CADB0547`. |
| A18 | Safe `ADD COLUMN` | `ADD COLUMN ... NOT NULL` with no `DEFAULT` on a table with rows | Add a `DEFAULT`, or add nullable, fill, then `SET NOT NULL`. Otherwise `CADB0301`. |
| A19 | Safe drops | `DROP COLUMN` of an indexed column; `DROP COLUMN`, `DROP TABLE`, or `TRUNCATE` that breaks a foreign key | Drop the index or constraint first. Otherwise `CADB0530`. |
| A20 | Name collisions | Two names that differ only in case; a sequence named like a table or view; database `_system` or `information_schema` | Rename. |
| A21 | Database exists | Table DDL before `CREATE DATABASE` | Add `CREATE DATABASE IF NOT EXISTS`. Otherwise `CADB0010`. |
| A22 | Owned sequence untouched | `DROP SEQUENCE` on an identity sequence | Drop the column instead. Otherwise `CADB0548`. |
| A23 | `JSONB` is not a key | `JSONB` column in a primary key, `UNIQUE`, ordered index key, or foreign key | Use a GIN index, or a typed column for the value. A key or an ordered index fails with `CADB0400`. |
| A24 | Valid GIN index | GIN index on a non-`JSONB` column, on two columns, with `INCLUDE`, or with `DESC` | One `JSONB` column only. Otherwise `CADB0400`. |
| A25 | No unsupported `JSONB` index | Expression index on `(doc ->> 'k')` | Not yet supported. Use a GIN index with `doc ->> 'k' = text`, or a typed column. |

## B. Hotspots and contention (HIGH)

| ID | Check | Detection | Fix |
| --- | --- | --- | --- |
| B1 | **[KRS]** Primary key does not always increase | First key column is `SERIAL`, `BIGSERIAL`, an identity, `nextval`, `DATETIME`, `DATE`, `gen_id()`, or `gen_uuid_v7()`, on a high-insert table | `UUID DEFAULT (gen_uuid_v4())`, or a composite key led by a spread column. |
| B2 | Sequence not used as the key of a high-write table | Sequence or identity primary key on a busy table, under any routing | Use `OID` or `UUID` as the key. Keep the sequence as a separate column if people need the number. |
| B3 | **[KRS]** Secondary index does not lead with time | Index whose first column is a time column or another increasing value | Put a spread or always-filtered column first: `(tenant_id, created_at)`. |
| B4 | **[KRS]** Leading index column has enough values | First column is low-cardinality, such as `kind` or `status` | Use `tenant_id`, `player_id`, or another high-cardinality column. Report as `MEDIUM`. |
| B5 | No single hot row | A global counter or config row updated by most transactions | Shard the counter: `PRIMARY KEY (name, shard)`. Sum on read. |
| B6 | Foreign-key parent is not hot | Parent row referenced by many children and updated often | Drop the foreign key, or move the changing columns to another table. |

## C. Integrity (HIGH or MEDIUM)

| ID | Severity | Check | Detection | Fix |
| --- | --- | --- | --- | --- |
| C1 | HIGH | Required columns are `NOT NULL` | A column the application treats as required has no `NOT NULL`; a `CHECK` without `NOT NULL` | Add `NOT NULL`. A `NULL` passes a `CHECK`. |
| C2 | HIGH | Money is exact | Money in `FLOAT64` or `FLOAT32` | Use `NUMERIC`. |
| C3 | MEDIUM | Enumerated values are checked | A status column with no rule | `CONSTRAINT t_valid_status CHECK (status IN ('a', 'b'))`. |
| C4 | MEDIUM | Foreign key added safely | `ADD CONSTRAINT ... FOREIGN KEY` on a table with live writers | Quiesce writers, or run an orphan check after the migration. |
| C5 | MEDIUM | TTL expiry is exact where needed | TTL table and the application relies on expired rows being gone | Filter `WHERE expires_at > NOW()` in reads. |
| C6 | MEDIUM | Array length checks count empty arrays | `CHECK` on `array_length(col, 1)` | Use `cardinality(col)`. |
| C7 | MEDIUM | Bounded text where a bound exists | Bare `STRING` for codes, emails, names | Use `STRING(N)`. Long values then fail with `CADB0302`. |
| C8 | MEDIUM | CTAS output has its constraints | `CREATE TABLE ... AS SELECT` and nothing after | Add indexes, constraints, defaults, and comments with `ALTER TABLE`. |
| C9 | HIGH | `CHECK` compares types with a rule | A `CHECK` compares a column with a value of another type that has no rule, such as `STRING` with a number | Write a `CAST`. Otherwise each write fails with `CADB0303`. |
| C10 | MEDIUM | Float-to-`INT64` cast in a `CHECK` | A stored `CHECK` with `CAST(float AS INT64)` | It rounds half away from zero, for new writes only. Existing rows are not checked again. |
| C11 | MEDIUM | Float `UNIQUE` or `NaN` rules are expected | `UNIQUE` on a float column; a query that assumes `NaN` compares false | `-0` equals `0` in a `UNIQUE` index. `NaN = NaN` is true and sorts highest. |

## D. Cost (MEDIUM)

| ID | Check | Detection | Fix |
| --- | --- | --- | --- |
| D1 | Each index serves a query | Index with no matching query; an index that is a left prefix of another; `SHOW INDEX USAGE` shows no reads over a long window | Drop it. Each index adds a mutation per insert and delete. Keep an index with `constraint_reads`. |
| D2 | Mutation budget fits | Bulk insert or delete of `R` rows with `K` indexes and `L` out-of-line values: `R x (2 + K + L) > 20,000` | Batch the work. Otherwise `CADB0506`. |
| D3 | Expiry uses TTL | Scheduled large `DELETE` of old rows | Use `ttl_expiration_expression`. |
| D4 | Hot columns apart from mid-size values | A value under 2048 bytes, such as a JSON document, beside a column updated often | Move the value to a separate table. |
| D5 | No side table only for a large value | A one-to-one table that only holds a large `STRING` or `BYTES` | Keep it in the row. `EXTENDED` stores it out of line. Report as `LOW`. |
| D6 | Storage strategy fits the reads | Embeddings for exact vector search not `PLAIN`; incompressible blobs not `EXTERNAL` | Set `STORAGE PLAIN` or `STORAGE EXTERNAL`. |
| D7 | Hot reads are covered | A hot query reads a few extra columns through an index | Add `INCLUDE (...)`. Confirm `index-only=true` in `EXPLAIN`. |
| D8 | Index order matches `ORDER BY` | `ORDER BY x DESC` with an `ASC` index on `x` | Create the index with the same directions. Scans are forward only. |
| D9 | No filter on `INCLUDE` as a bound | A query filters by an `INCLUDE` column and expects a seek | Move the column into the key. |
| D10 | Native identifier types | UUID or ObjectId text in `STRING` | Use `UUID` or `OID`. |
| D11 | Sequence cache sized | Busy free-standing sequence with `CACHE 1` | Declare a larger `CACHE n` in `CREATE SEQUENCE` if gaps are acceptable. Unused cached values can be skipped. |
| D12 | Foreign-key index exists | Child foreign key with no index on its columns | Fine, CamusDB creates a `~fk_` index. Count it as one more write per insert. Report as `LOW`. |
| D13 | Right column for JSON | JSON in `STRING` when queries read inside it; `JSONB` when the exact input text must survive | Use `JSONB` to query inside. Use `STRING` to keep the text. |
| D14 | GIN index fits its documents | GIN index over documents with many leaves, or a key that every document holds | Each distinct leaf costs one write and counts toward `CADB0506`. A frequent key takes a wide range lock under Serializable. |

## E. Documentation (LOW)

| ID | Check | Detection | Fix |
| --- | --- | --- | --- |
| E1 | Tables, columns, and indexes have comments | No `COMMENT` clause | Add `COMMENT '...'`. Agents read them through MCP. |
| E2 | Constraints have stable names | Unnamed `CHECK` or foreign key that a migration later drops | Name it: `CONSTRAINT name CHECK (...)`. |
| E3 | Identity choice is clear | `SERIAL` where `GENERATED ALWAYS AS IDENTITY` fits | Use `ALWAYS` when callers must never supply the value. |
