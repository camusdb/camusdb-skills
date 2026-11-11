# Online DDL in CamusDB

Sources: https://camusdb.github.io/docs/sql-schema,
https://camusdb.github.io/docs/distributed-schema,
https://camusdb.github.io/docs/sql-indexes,
https://camusdb.github.io/docs/foreign-keys,
https://camusdb.github.io/docs/check-constraints,
https://camusdb.github.io/docs/error-codes

## How a DDL statement runs

In cluster mode the schema is replicated state. The committed schema log in
Kommander (Raft) is the source of truth. A DDL statement follows this path:

1. A client sends the statement to any node.
2. The node forwards it to the schema leader. The request carries a stable
   operation id, so a retry after a lost response does not apply twice.
3. The leader validates it against the current schema version.
4. The change is written as a replicated log entry and committed by Raft.
5. Each node applies the change locally.
6. The leader waits for live nodes that hold the database open to acknowledge
   the version before the next stage.

Versions only increase. A database has a schema version counter. Each table has
its own version. Each stored row keeps the schema version of its write.

### Staged states

| State | Meaning |
| --- | --- |
| `DeleteOnly` | The element exists in metadata. Normal reads and writes ignore it. |
| `WriteOnly` | New writes maintain the element. Reads do not depend on it yet. |
| `Public` | The element is fully visible to queries. |

- Adding a column or an index is staged: metadata, then writes, then backfill,
  then publish.
- Removing a column can be staged in reverse order.
- Removing an index is one replicated schema change. It is not staged.

### Backfill

- A new column with a default is backfilled before it becomes `Public`.
- A new index is backfilled before the planner can use it.
- The backfill coordinator checkpoints progress. A new schema leader resumes
  from the checkpoint after a restart or leader change.
- Each batch locks its rows, reads them again, writes, and commits. The locks
  apply under any configured locking mode. A concurrent update is not
  overwritten, and a concurrent delete is not undone.
- A batch that loses a lock is retried with no attempt limit. It halves on each
  loss and doubles on each win. Only a non-retryable error, such as a duplicate
  key or a cancellation, ends the build.
- Column fills use batches of 256 rows. Index builds scan 500 rows per batch.
- During a fill, an `UPDATE` that does not name the new column is accepted. The
  `NOT NULL` rule applies only to a value a statement assigns, until `Public`.
- A column fill also fills a `NULL` in a column with a default. No statement can
  name the column before it is readable, so that `NULL` is not a user's value.
- Internal DDL and backfill transactions are exempt from
  `max_mutations_per_transaction`. User DML is always counted.
- A column fill gives each existing row the value an `INSERT` would give it.
  A function default such as `gen_uuid_v7()` runs once per row.
- Plan for load. One measured 20,000-row index build took 40 s under a bank
  workload. The workload ran at 52 percent of its throughput during the build.
- A read through an index checks the fetched row against the entry and skips a
  stale entry. It counts in `camus.index_read.stale_entries`. A covering read has
  no row to check, so it trusts the entry.

### The DDL gate

- Each database serializes its schema proposals. Each proposal is short.
- A staged change holds its table for the whole job: a cluster `CREATE INDEX`,
  `ADD COLUMN`, a foreign key validation, and a cluster `CHECK` or `SET NOT NULL`
  validation. The gate is released during the row pass.
- DDL on another table, `CREATE TABLE` included, runs at once.
- DDL on the same table waits, then runs.
- `DROP DATABASE` and a branch create wait for every staged change.
- On a standalone node, `CREATE INDEX` and `ADD CHECK` or `SET NOT NULL` hold the
  gate for their full run. Other DDL in that database waits.

### Failure and handover

- A failed `ADD COLUMN` or `ADD CONSTRAINT` leaves no element and no constraint.
  The same statement can run again.
- A job is resumed by the first of three triggers: the node becomes schema
  leader, the database opens on a node that leads, or the leader's sweep finds
  it. A failed attempt backs off, doubling from 5 s up to 2 minutes.
- After 8 failed attempts the job is abandoned and logged. The element stays in
  its current state. Drop it by hand before you create it again.
- If the schema leader moves, the old leader stops at its next batch. The
  statement answers from what the new leader did:

  | New leader's result | Statement result |
  | --- | --- |
  | Published the element | Success. Do not rerun it. |
  | Took the element back | Fails with the recorded error. It can run again. |
  | Still driving after ten minutes | Fails with an error that says so. Check the schema before you rerun. |

### Convergence and slow nodes

- The leader first waits for every live node to apply a stage.
- After a bounded delay, a majority is enough to continue.
- A node more than one schema version behind rejects reads and DML for that
  database with `CADB0503 SchemaCatchingUp`. Retry later or on another node.
- Live membership comes from Raft. A dead node does not block DDL forever.

Related settings (YAML, see https://camusdb.github.io/docs/configuration):

| Key | Default | Meaning |
| --- | --- | --- |
| `schema_ack_wait_timeout_ms` | `30000` | How long a DDL proposer waits for all acks. |
| `schema_ack_live_node_lease_ms` | `30000` | How long since last contact before a member is presumed down. |
| `schema_freshness_check_interval_ms` | `10000` | Cluster sweep that reloads a stale in-memory schema. `0` or below disables it. |
| `coordinator_job_sweep_interval_ms` | `10000` | Leader sweep that resumes interrupted schema jobs. `0` or below disables it. |

### Transactions and DDL

- A transaction pins the schema versions it uses. A later DDL makes a later
  statement or the commit fail with the retryable `CADB0502`. The transaction is
  rolled back. CamusDB does not mix two layouts. Retry from `BEGIN`.
- `TRUNCATE` is refused inside an explicit transaction (`CADB0538`).
- `CREATE TABLE ... AS SELECT` commits the table first, then loads rows in the
  caller's transaction. A failed load drops the table again, but an empty
  table can remain if that compensating drop fails.
- The CTAS source can be a plain view, also `WITH NO DATA`. A scalar subquery
  column keeps its declared type, even when it has no row.

## Statement catalog

### Tables

```sql
CREATE TABLE IF NOT EXISTS robots (
  id OID PRIMARY KEY NOT NULL DEFAULT (gen_id()),
  name STRING NOT NULL
);
ALTER TABLE robots RENAME TO machines;
DROP TABLE robots;              -- recoverable orphan
DROP TABLE IF EXISTS robots;
DROP TABLE robots FORCE;        -- permanent, no orphan
```

### Columns

```sql
ALTER TABLE robots ADD COLUMN model STRING NULL;
ALTER TABLE robots ADD COLUMN notes STRING NULL COMMENT 'Operator notes';
ALTER TABLE robots DROP COLUMN model;
ALTER TABLE machines RENAME COLUMN name TO display_name;
ALTER TABLE employees ALTER COLUMN name SET NOT NULL;
ALTER TABLE employees ALTER COLUMN name DROP NOT NULL;
ALTER TABLE docs ALTER COLUMN image SET STORAGE PLAIN;
ALTER TABLE docs REWRITE STORAGE;
```

- `ADD COLUMN ... NOT NULL` on a table with rows needs a non-NULL default
  (constant, function, or sequence). Otherwise it fails with `CADB0301
  NotNullViolation`. The error text suggests the alternative: add the column
  without `NOT NULL`, fill it, then `SET NOT NULL`.
- `SET NOT NULL` scans existing rows first. The rule is enforced on new writes
  before the scan starts.
- You cannot `DROP NOT NULL` on a primary-key column.
- `SET STORAGE` affects future writes only. `REWRITE STORAGE` converts existing
  rows, in batches of `large_value_rewrite_batch_rows` (default `200`).
- A rename to an existing column name fails with `DuplicateColumn`
  (`CADB0403`). A rename of a missing column fails with `UnknownColumn`
  (`CADB0404`).

### Primary keys

```sql
ALTER TABLE robots ADD PRIMARY KEY (id);
ALTER TABLE robots DROP PRIMARY KEY;
```

Primary-key columns are always `NOT NULL`.

### Indexes

```sql
CREATE INDEX robots_year_idx ON robots (year DESC);
CREATE UNIQUE INDEX robots_name_idx ON robots (name);
CREATE INDEX robots_kind_idx ON robots (kind) INCLUDE (name, year);
ALTER TABLE robots ADD INDEX robots_kind_year_idx (kind, year DESC);
ALTER TABLE robots ADD UNIQUE INDEX robots_code_year_uq (code, year);
ALTER TABLE robots DROP INDEX robots_kind_year_idx;
DROP INDEX robots_kind_idx ON robots;
DROP INDEX IF EXISTS robots.robots_year_idx;
CREATE INDEX events_payload_gin ON events USING GIN (payload);
ALTER TABLE robots RENAME INDEX robots_name_idx TO robots_display_name_idx;
```

- Descending order works for fixed-width types only: `OID`, `UUID`, `INT64`,
  `FLOAT64`, `FLOAT32`, `BOOL`, `DATE`, `DATETIME`. A descending `STRING` or
  `BYTES` column fails with `InvalidInput`.
- An `ARRAY(T)` column cannot be indexed.
- A bare `DROP INDEX name` fails with `InvalidInput`. Use `DROP INDEX name ON t`,
  `DROP INDEX t.name`, or `ALTER TABLE t DROP INDEX name`.
- `IF EXISTS` hides a missing table or index only. It does not hide a refusal
  from a foreign key (`CADB0530`). A no-op changes no schema version.
- `DROP INDEX` needs the `INDEX` privilege. Its work grows with the entry count.
- A `JSONB` column takes a GIN index only. It has one column, no `INCLUDE`, and
  no `DESC`. An ordered index on `JSONB` fails with `InvalidInput`.
- One index spans at most 32 columns (key plus `INCLUDE`). The `INCLUDE` payload
  is limited to 4 KiB per entry. Settings: `max_index_columns`,
  `max_index_include_tuple_bytes`.
- To change an `INCLUDE` list, drop the index and create it again.
- An index rename of a missing index, or to an existing name, fails with
  `InvalidInput`.
- Before a `UNIQUE` index on existing data, look for duplicates:

  ```sql
  SELECT code, year, COUNT(*) FROM robots GROUP BY code, year HAVING COUNT(*) > 1;
  ```

### Constraints

```sql
ALTER TABLE products ADD CONSTRAINT positive_price CHECK (price > 0);
ALTER TABLE products DROP CONSTRAINT positive_price;
ALTER TABLE weather ADD CONSTRAINT weather_city_fk
  FOREIGN KEY (city) REFERENCES cities (name);
ALTER TABLE weather DROP CONSTRAINT weather_city_fk;
```

- `ADD CONSTRAINT ... CHECK` scans existing rows and fails on a violation
  (`CADB0303`). The new check applies to concurrent writes during the scan.
- `ADD CONSTRAINT ... FOREIGN KEY` validates existing rows. It needs `SELECT` on
  the parent. Quiesce child writers, or run an orphan check afterwards:

  ```sql
  SELECT id FROM weather
  WHERE city IS NOT NULL AND city NOT IN (SELECT name FROM cities);
  ```

  Use `NOT EXISTS` instead of `NOT IN` when the inner column can hold `NULL`.

### Table settings

```sql
ALTER TABLE application_logs SET (sql_stats_automatic_collection_enabled = false);
ALTER TABLE sessions SET (ttl_expiration_expression = 'expires_at', ttl_job_cron = '@hourly');
ALTER TABLE sessions RESET (ttl);
```

An unknown setting name is rejected.

## Changes blocked by foreign keys

All refused changes fail with `CADB0530 DependentObjectsExist`.

| Operation | Result |
| --- | --- |
| `DROP TABLE` of a referenced parent | Refused. Drop the child or the constraint first. |
| `DROP TABLE` of a child | Accepted. Its foreign keys go with it. |
| `TRUNCATE` of a referenced parent | Refused. |
| `TRUNCATE` of a child | Accepted. |
| `DROP COLUMN` of a referencing or referenced column | Refused. |
| `DROP INDEX` of the referenced or backing index | Refused. |
| `RENAME` of a table, column, or index | Accepted. Constraints track ids. |
| `CREATE TABLE ... AS SELECT` | The new table gets no foreign keys. |
| `CREATE TABLE ... RELINK TO` of a child | Comes back without foreign keys. |

Dependent views also raise `CADB0530`. You also cannot drop a column that an
index uses as a key or `INCLUDE` column.

## Patterns for common migrations

### Add a required column

```sql
-- Option A: one statement; backfill is internal and unlimited.
ALTER TABLE orders ADD COLUMN currency STRING NOT NULL DEFAULT ('USD');

-- Option B: computed values.
ALTER TABLE orders ADD COLUMN region STRING NULL;
UPDATE orders SET region = "eu" WHERE region IS NULL AND country = "ES" LIMIT 5000;
-- repeat each batch until 0 rows, for every value, then:
ALTER TABLE orders ALTER COLUMN region SET NOT NULL;
```

Check the default syntax with your server on the branch first. The docs show
defaults as `DEFAULT (<expression>)`.

### Change a column type

There is no documented `ALTER COLUMN ... TYPE`. Use expand and contract:

1. `ADD COLUMN new_col <type> NULL`.
2. Backfill in batches under the mutation limit.
3. Deploy readers and writers that use `new_col`.
4. Copy any values written since step 2.
5. Confirm with the user, then `DROP COLUMN old_col`. This is irreversible.
6. Optionally `RENAME COLUMN new_col TO old_col`.

### Replace an index

Create the new index first, verify the plan with `EXPLAIN`, then drop the old
index. Never drop first on a live table.

### Rebuild a float index after an upgrade

An index built before the current float rules holds old keys for `-0` and `NaN`.
If the float column holds either value, rebuild the index with `DROP INDEX` and
`CREATE INDEX`. An index on a column that holds neither needs no rebuild.

## Error codes for DDL

| Code | Name | Meaning and action |
| --- | --- | --- |
| `CADB0011` | `TableDoesntExist` | Table missing. In a cluster CamusDB reloads a stale schema before returning it. |
| `CADB0013` | `TableAlreadyExists` | Name taken by a table. |
| `CADB0099` | `InvalidInternalOperation` | A schema proposal timed out or failed. The entry may still commit. Read the schema before you rerun. |
| `CADB0301` | `NotNullViolation` | `NULL` in a `NOT NULL` column, including `ADD COLUMN ... NOT NULL` without default and `SET NOT NULL` over existing `NULL`s. |
| `CADB0303` | `CheckConstraintViolation` | A row violates a `CHECK`, including during `ADD CONSTRAINT`. |
| `CADB0400` | `InvalidInput` | Invalid DDL shape, invalid index rename, bare `DROP INDEX name`, invalid CTAS projection, branch precondition failure. |
| `CADB0403` | `DuplicateColumn` | Column name used twice, or a rename to an existing name. |
| `CADB0404` | `UnknownColumn` | Column not present or not visible yet. |
| `CADB0408` | `SchemaLimitExceeded` | Name too long, or too many columns, indexes, or tables. |
| `CADB0415` | `InvalidForeignKeyDefinition` | Bad foreign-key target. |
| `CADB0416` | `ForeignKeyCycle` | Foreign keys would form a cycle across tables. |
| `CADB0502` | `TransactionConflict` | A transaction's pinned schema version changed. Retry from `BEGIN`. |
| `CADB0503` | `SchemaCatchingUp` | This node lags the schema head. Retry later or elsewhere. |
| `CADB0504` | `TransactionMustRetry` | The schema leader moved during the proposal. Nothing was committed. Rerun the statement. |
| `CADB0530` | `DependentObjectsExist` | A view or foreign key depends on the target. |
| `CADB0534` | `ConcurrentSchemaChange` | The definition changed underneath. Nothing applied. Retry against the current definition. |
| `CADB0538` | `StatementNotAllowedInTransaction` | `TRUNCATE` inside `BEGIN`. |
