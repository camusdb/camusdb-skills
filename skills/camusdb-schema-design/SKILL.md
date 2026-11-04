---
name: camusdb-schema-design
description: Designs and reviews CamusDB schemas. Covers CREATE TABLE, primary keys, CREATE INDEX with INCLUDE covering columns, GIN indexes, DROP INDEX, column types (OID, UUID, INT64, NUMERIC, STRING(N), BYTES, DATETIME, ARRAY, JSONB), defaults such as gen_id(), gen_uuid_v4() and gen_uuid_v7(), CHECK and FOREIGN KEY constraints, CREATE SEQUENCE, SERIAL and identity columns, STORAGE strategies for large values, row-level TTL (ttl_expiration_expression), and COMMENT ON. Applies the CamusDB best practices for hotspots under hash routing and key_range_sharding. Use when writing or changing CamusDB DDL or a migration, when choosing a primary key, an index, or a column type, when auditing an existing schema or migration file (review mode), or when DDL fails with CADB0400, CADB0302, CADB0414, CADB0415, CADB0416, CADB0530 or CADB0533, or writes hit CADB0418, CADB0420, CADB0421, CADB0502 on a hot row or CADB0506 from too many index mutations.
---

# CamusDB schema design

Use this skill to design a new CamusDB schema, or to review an existing one.
CamusDB is a distributed NewSQL database. Its placement model differs from
PostgreSQL and MySQL. A key that is fine there can create a hotspot here.
Every rule below comes from the CamusDB docs. Do not add a feature that the
docs do not describe.

The skill has two modes:

- **Design mode.** The user wants new tables, indexes, or a migration.
- **Review mode.** The user gives a schema, a `SHOW CREATE TABLE` output, or a
  migration file. Audit it and report findings with fixes.

## Workflow: design mode

1. Find the routing mode. Hash routing is the default. A cluster records its
   mode once, when it is created. On a running cluster, check
   `key_range_sharding` in `SHOW VARIABLES`. For a new cluster, read
   `key_range_sharding` in `config.yml` and `CAMUS_KEY_RANGE_SHARDING`. If you
   cannot tell, ask. If no answer comes, design for key-range sharding. That
   design also works under hash routing.
2. List the queries and the write rate of each table. Name the filter columns
   and the `ORDER BY` of each hot query.
3. Choose the primary key. Use the table in [Primary keys](#primary-keys).
4. Choose each column type. Read
   [references/data-types.md](references/data-types.md) for aliases, limits,
   and literal formats.
5. Add a secondary index only for a real query. Put a column with a good spread
   first. Add `INCLUDE (...)` for a hot read path.
6. Add constraints: `NOT NULL`, `CHECK`, unique keys, and foreign keys. Weigh
   the write cost of each foreign key.
7. Check the row layout: large values, columns that change often, hot rows.
   Use row-level TTL for data that expires. Add `COMMENT` clauses.
8. Write the DDL. Check the syntax in
   [references/ddl-syntax.md](references/ddl-syntax.md).
9. Tell the user how to verify: `SHOW CREATE TABLE`, `SHOW INDEXES FROM t`,
   `EXPLAIN (ANALYZE)` on the hot queries, and `SHOW RANGES FROM TABLE t`.

## Workflow: review mode

1. Read the whole schema or migration first. Note the statement order.
2. Find the routing mode, as in design step 1. Mark each finding that applies
   only under key-range sharding.
3. Walk every item of [references/review-checklist.md](references/review-checklist.md).
4. Check each statement against [references/ddl-syntax.md](references/ddl-syntax.md).
   Syntax from another database often fails in CamusDB.
5. Report the findings in a table: ID, severity, location, problem, fix.
   Give the fix as CamusDB SQL. Use the severities and the report format in
   the checklist file.
6. Order the findings by severity. Put statements that CamusDB rejects first.
7. End with a short list of the checks that passed, so the user sees coverage.

## How CamusDB places data

- **Hash routing** is the default. All rows of a table, and all of its
  secondary indexes, live on one Raft partition. The key choice does not spread
  one table across partitions.
- **Key-range sharding** routes each key space by key order. The rows, the
  large values, and each secondary index are separate key spaces. Each starts
  as one range and can split into ranges on different partitions.
- CamusDB stores a row under an internal row id, not under the primary key.
  The row id is an ObjectId with a timestamp prefix.
- The primary key is a unique index that maps each key to its row id.
- With key-range sharding, new rows always go to the newest range of the row
  space. The primary key choice spreads the primary index and the secondary
  indexes only.
- With key-range sharding, a new table stays on one partition until its first
  split. Indexes of every column type can split, `STRING` included.
- The routing mode cannot change on existing data. A node set to the other mode
  refuses to start (`CADB0600`). To move, dump and reimport into a new cluster.

See [Best practices](https://camusdb.github.io/docs/best-practices) and
[Key-range sharding](https://camusdb.github.io/docs/key-range-sharding).

## Primary keys

Every table needs a primary key. CamusDB adds no hidden key. A table without
one fails with `CADB0400: A primary key column is mandatory in the table`.

Choose the key generator by routing mode and insert rate:

| Case | Key |
| --- | --- |
| Key-range sharding, high insert rate | `UUID DEFAULT (gen_uuid_v4())` |
| Hash routing | `OID DEFAULT (gen_id())` or `UUID DEFAULT (gen_uuid_v7())` |
| Low insert rate, recent rows read together | `gen_id()` or `gen_uuid_v7()` |
| Rows always read per entity | Composite key: entity column first, order column second |
| Human-facing number (invoice, ticket) | Keep an `OID`/`UUID` key. Add an identity column. |

These keys always increase. Avoid them as the first key column under
key-range sharding:

- A sequence, an identity column, `SERIAL`, and `BIGSERIAL`.
- A `DATETIME` or `DATE` column.
- An `OID` from `gen_id()`. It starts with a timestamp in seconds.
- A `UUID` from `gen_uuid_v7()`. It starts with a timestamp in milliseconds.

A sequence also costs coordination under any routing mode. It is one logical
counter, and every insert that needs a value goes through it.

```sql
CREATE TABLE players (
  id UUID PRIMARY KEY NOT NULL DEFAULT (gen_uuid_v4()),
  name STRING(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT (now())
);
```

Match a composite key to the access pattern. Put the spread column first:

```sql
CREATE TABLE match_events (
  player_id UUID NOT NULL,
  created_at DATETIME NOT NULL DEFAULT (now()),
  kind STRING(32) NOT NULL,
  score INT64 NOT NULL
) PRIMARY KEY (player_id, created_at DESC);
```

Key rules:

- Primary key columns are always `NOT NULL`, even when the DDL omits it.
- A primary key does not support `INCLUDE`.
- An `ARRAY(T)` column cannot be in a primary key.
- `DESC` works only on fixed-width types. A `DESC` `STRING` or `BYTES` key
  column fails with `InvalidInput`.

## Secondary indexes

The hotspot rule applies to each index under key-range sharding. An index that
starts with a time column sends every insert to its last range:

```sql
-- Hotspot: every insert writes the last range of this index.
CREATE INDEX match_events_created_idx ON match_events (created_at);

-- Better: a spread or always-filtered column first.
CREATE INDEX match_events_kind_idx ON match_events (kind, created_at);
```

A low-cardinality column such as `kind` spreads writes only across its
values. `player_id` or `tenant_id` is better. There is no hash-sharded index.

Use a covering index on a hot read path. The keyword is `INCLUDE`, not
`STORING`: `CREATE INDEX i ON match_events (kind, created_at) INCLUDE (score)`.
`EXPLAIN` shows `index-only=true` for a covered scan. `EXPLAIN (ANALYZE)` shows
`rows_read = 0`.

Index rules:

- Add an index only for a real query. Each index adds one mutation per insert
  and per delete. An update of an indexed column also writes the index.
- One index spans at most 32 columns, key and included together. The included
  payload is at most 4 KiB per index entry.
- An included column cannot also be a key column. It takes no `ASC`/`DESC`.
- A predicate on an included column is a filter, not an index bound.
- `ORDER BY` must match a left prefix of the index, with the same directions.
  CamusDB scans an index forward only.
- An `ARRAY(T)` column cannot be an index key. It can be an `INCLUDE` column.
- A `JSONB` column cannot be an ordered index key. Use a GIN index:
  `CREATE INDEX i ON t USING GIN (doc)`.
- Drop an index with `DROP INDEX name ON t`, `DROP INDEX t.name`, or
  `ALTER TABLE t DROP INDEX name`. Each form takes `IF EXISTS`. A bare
  `DROP INDEX name` is refused. To change `INCLUDE`, drop the index and create
  it again.

See [Indexes](https://camusdb.github.io/docs/sql-indexes).

## Column types

Read [references/data-types.md](references/data-types.md) for the full table.
The rules that matter most:

- `UUID` for UUIDs, `OID` for ObjectIds. Both are compact and indexable.
- `NUMERIC` for money. It has fixed precision 38 and scale 9.
  `NUMERIC(10, 2)` is rejected.
- `STRING(N)` and `BYTES(N)` are maximums. A longer value fails with
  `CADB0302 ValueTooLong`. CamusDB never truncates.
- A bare `STRING` allows 2,621,440 characters. A bare `BYTES` allows
  10,485,760 bytes.
- `DATETIME` is an instant in UTC. `TIMESTAMP` is an alias.
- The type list has no `INTERVAL` type.
- `ARRAY(T)` holds scalars only. No nested arrays, no `ARRAY(NUMERIC)`, and no
  `ARRAY(JSONB)`. An array of documents is one `JSONB` document.
- `INT64` overflow fails with `CADB0420`. `FLOAT64` and `FLOAT32` overflow
  fails with `CADB0421`. Neither wraps or becomes infinity.

Choose `JSONB` (alias `JSON`) when queries read inside the document. Choose
`STRING` when you must keep the exact input text. `JSONB` sorts keys, drops
duplicate keys, and rejects invalid JSON with `CADB0418`.

A `JSONB` column cannot be in a primary key, a `UNIQUE` constraint, an ordered
index, or a foreign key. To sort on a value, enforce it, or reference it, copy
it to a typed column. See [JSON](https://camusdb.github.io/docs/json).

## Rows, large values, and hot rows

A row is the unit of storage and of versions. An update of one column writes a
new version of the whole row.

- The default `EXTENDED` strategy moves a `STRING`, `BYTES`, `JSONB`, or `ARRAY`
  value of 2048 bytes or more (`large_value_threshold_bytes`) under its own key. Do not
  create a side table only to hold a large value.
- A smaller value stays in the row. Move it to a separate table when it sits
  beside a column that changes often. Example: a 1 KiB JSON profile next to a
  per-match counter.
- Use `STORAGE PLAIN` for values that every query reads, such as embeddings for
  exact vector search. Use `STORAGE EXTERNAL` for incompressible blobs that
  most queries skip.
- Each out-of-line value adds one mutation on insert and on delete.
- `SELECT *` fetches every out-of-line value. Name the columns you need.

Avoid a single hot row. A global counter makes all writers conflict, and they
abort with `CADB0502 TransactionConflict`. Shard the counter with
`PRIMARY KEY (name, shard)`. Each writer updates one shard. Readers
`SUM(value)`. See [Large values](https://camusdb.github.io/docs/storage-layout).

## Constraints

- **NOT NULL.** Declare it on every required column. `CHECK` alone accepts
  `NULL`, because an unknown result passes.
- **CHECK.** One-row deterministic predicates only. No subquery, aggregate, or
  volatile function (`now()`, `gen_id()`, `gen_uuid_v4()`, `gen_uuid_v7()`).
  Name a check at table level: `CONSTRAINT name CHECK (...)`. A named
  column-level check is not supported.
- **UNIQUE.** Use a column-level `UNIQUE`, an inline `UNIQUE KEY name (cols)`,
  or `CREATE UNIQUE INDEX`. Uniqueness applies to key columns only. A `NULL`
  key never conflicts. `INSERT ... ON CONFLICT (a, b)` needs a unique index on
  exactly those columns, and `UPSERT` uses the primary key.
- **FOREIGN KEY.** Only `NO ACTION` (default) and `RESTRICT`. `CASCADE`,
  `SET NULL`, `SET DEFAULT`, `MATCH FULL`, and `DEFERRABLE` fail with
  `CADB0533`. A cycle across two or more tables fails with `CADB0416`.

Foreign-key cost on writes:

- A child write takes a shared lock on each distinct parent key until commit.
  A parent that many children reference, and that changes often, becomes a
  contention point.
- The child needs an index on the referencing columns. CamusDB creates an
  owned `~fk_` index when none fits. That is one more write per child insert.
- A table with row-level TTL cannot be a foreign-key parent.

See [Check constraints](https://camusdb.github.io/docs/check-constraints) and
[Foreign keys](https://camusdb.github.io/docs/foreign-keys).

## Sequences and identity columns

Use a sequence for a number people see: an invoice, an order, a ticket. Do not
use one as the primary key of a high-write table. Keep an `OID` or `UUID` key,
and add `invoice_no INT64 GENERATED BY DEFAULT AS IDENTITY`.

- `SERIAL` and `BIGSERIAL` are `INT64` identity shorthand, not storage types.
- Sequence values have gaps and are not a perfect insert order. `CYCLE` is not
  supported. `nextval` elsewhere than `INSERT ... VALUES`, a column default, or
  a `SELECT` without `FROM` fails with `CADB0547`.

See [Sequences](https://camusdb.github.io/docs/sequences).

## Row-level TTL

Use TTL instead of large batch deletes. Name a `DATETIME`, `DATE`, or `INT64`
(Unix epoch milliseconds) column:

```sql
CREATE TABLE sessions (
  id UUID PRIMARY KEY NOT NULL DEFAULT (gen_uuid_v4()),
  player_id UUID NOT NULL,
  expires_at DATETIME NOT NULL
) WITH (ttl_expiration_expression = 'expires_at');
```

- An expired row stays readable until the sweep deletes it. Filter with
  `WHERE expires_at > NOW()` when the expiry must be exact.
- A `NULL` expiry never expires. The setting takes a bare column name only.
- Each deleted row costs one mutation, plus one per index entry.

See [Row-level TTL](https://camusdb.github.io/docs/row-level-ttl).

## Comments

Add a `COMMENT '...'` clause to each table, column, and inline index. Agents
that use the CamusDB MCP server read them. A comment holds at most 65,535
characters (`CADB0511`). See [Schema comments](https://camusdb.github.io/docs/comment-on).

## Schema changes

- `ALTER TABLE ... ADD COLUMN c T NOT NULL` needs a `DEFAULT` when the table
  has rows. Otherwise it fails with `CADB0301`.
- CamusDB rejects `DROP COLUMN` of a column that an index uses. Drop the index
  first.
- Add a foreign key while child writers are quiet, or run an orphan check
  after the migration.
- `CREATE TABLE ... AS SELECT` copies no index, constraint, default, comment,
  or setting.
- See [references/ddl-syntax.md](references/ddl-syntax.md) for the other
  `ALTER TABLE` rules.

## Common mistakes

Habits from other databases that are wrong in CamusDB:

| Habit | Source | CamusDB rule |
| --- | --- | --- |
| Table without a primary key | MySQL, PostgreSQL | Rejected with `CADB0400`. |
| `id SERIAL PRIMARY KEY` / `AUTO_INCREMENT` key | PostgreSQL, MySQL | Use `OID`/`UUID`. Keep sequences for human numbers. |
| `STORING (...)` covering index | CockroachDB | Use `INCLUDE (...)`. |
| Hash-sharded index, bit-reversed sequence | CockroachDB, Spanner | Not available. Use `gen_uuid_v4()` or a spread leading column. |
| `NUMERIC(10, 2)` | All | Rejected. `NUMERIC` is fixed at precision 38, scale 9. |
| UUID in `VARCHAR(36)` | MySQL | Use a native `UUID` column. |
| `JSON` to keep the input text | PostgreSQL | `JSON` is an alias of `JSONB`. Keys are sorted. Use `STRING` for exact text. |
| Ordered index or key on a `JSONB` column | All | Refused with `CADB0400`. Use a GIN index or a typed column. |
| `ON DELETE CASCADE` / `SET NULL` | All | Refused with `CADB0533`. Delete children in the application. |
| `DEFERRABLE INITIALLY DEFERRED` | PostgreSQL | Refused with `CADB0533`. |
| `"identifier"` in double quotes | PostgreSQL | Double quotes make a string literal. Quote identifiers with backticks. |
| Bare `DROP INDEX name` | PostgreSQL | Refused with `CADB0400`. Use `DROP INDEX name ON t` or `DROP INDEX t.name`. |
| `CONSTRAINT u UNIQUE (a, b)` | PostgreSQL | Use `UNIQUE KEY u (a, b)` or `CREATE UNIQUE INDEX`. |
| `DESC` index on a `STRING` column | All | Rejected. `DESC` works on fixed-width types only. |
| Expecting TTL to hide expired rows | Cassandra, Redis | Filter on the expiry column in queries. |
| `ttl_expire_after = '30 days'` | CockroachDB | Not supported. Use `ttl_expiration_expression` with your own column. |
| Index on `(created_at)` alone | All | A hotspot under key-range sharding. Lead with a spread column. |
| One big `DELETE` for cleanup | All | Hits `CADB0506` at 20,000 mutations. Use TTL or batches. |
| Long `TEXT` column truncated silently | MySQL | CamusDB fails with `CADB0302`. It never truncates. |

## References

Read these files when the task needs the detail:

- [references/data-types.md](references/data-types.md): read when you choose a
  type, write a literal, or check a length limit.
- [references/ddl-syntax.md](references/ddl-syntax.md): read before you write
  or review any DDL statement.
- [references/review-checklist.md](references/review-checklist.md): read in
  review mode. Walk every item.
- [references/limits-and-errors.md](references/limits-and-errors.md): read when
  a statement fails, or when you size a transaction or an index.

Public docs, under `https://camusdb.github.io/docs/`: `best-practices`,
`data-types`, `json`, `sql-schema`, `sql-indexes`, `sql-writes`, `sequences`,
`foreign-keys`, `check-constraints`, `row-level-ttl`, `storage-layout`, `storage`,
`key-range-sharding`, `functions-uuid`, `functions-object-id`, `comment-on`,
`transaction-limits`, and `databases`.
