---
name: camusdb-migrations-and-recovery
description: Plans and runs safe CamusDB schema changes and recovers lost data. Covers online DDL (ALTER TABLE, CREATE INDEX, DeleteOnly/WriteOnly/Public states, backfill), migration rehearsal on a branch (CREATE DATABASE ... BRANCH FROM, SHOW BRANCHES, SHOW ANCESTORS), batched backfills under the 20,000-mutation limit, AS OF SYSTEM TIME recovery, SHOW ORPHAN TABLES, SHOW ORPHAN DATABASES and RELINK TO, TRUNCATE recovery, physical backups and PITR (/v1/backups, /v1/restore, camus-cli backup), camus-dump, logical dump and reimport across storage revisions, and WAL crash recovery. Use when writing or applying a migration, after an accidental DROP, DELETE, UPDATE or TRUNCATE, when setting up backups or retention, or when you see CADB0506, CADB0530, CADB0534, CADB0537, CADB0539, CADB0510, CADB0503 or a CADB07xx backup error.
---

# CamusDB migrations and recovery

This skill helps you change a CamusDB schema without surprises, and get data
back after a mistake. CamusDB runs DDL online through a replicated schema log.
It keeps dropped objects as recoverable orphans, keeps MVCC history for
time-travel reads, and offers physical backups with point-in-time restore.
Each tool has a different window and a different cost. Pick the right one first.

Public docs: https://camusdb.github.io/docs/intro

## Safety rules (always apply)

- Confirm with the user before any destructive or irreversible command. This
  includes `DROP DATABASE`, `DROP TABLE`, `TRUNCATE`, `DELETE`, a wide `UPDATE`,
  `ALTER TABLE ... DROP COLUMN`, `ALTER TABLE ... DROP INDEX`, `RESET CLUSTER
  SETTING`, backup `gc`, and any restore step.
- Never add `FORCE` to a `DROP` unless the user asks for a permanent delete.
  A forced drop creates no orphan and cannot be recovered.
- Rehearse every migration on a branch first. Apply to the real database only
  after the branch run passes.
- Record an absolute UTC timestamp before you apply a risky change. It is your
  `AS OF SYSTEM TIME` anchor if something goes wrong.
- Recover into a new name first. Inspect it. Only then copy data back.

## Pick the right tool

| Situation | Tool | Window |
| --- | --- | --- |
| Rows changed or deleted by mistake | `AS OF SYSTEM TIME` + `INSERT ... SELECT` / CTAS | MVCC history, about 1 hour by default |
| `DROP TABLE` or `DROP DATABASE` (root database) | `SHOW ORPHAN ...` + `RELINK TO` | `orphan_retention_ms`, 7 days by default |
| `TRUNCATE TABLE` | `SHOW ORPHAN TABLES` (`retired contents`) + `RELINK TO` | `orphan_retention_ms` |
| Node or cluster data loss, or a point before the MVCC window | Physical backup + offline restore | The chain's recoverable range |
| Move data to another machine or storage revision | `camus-dump` + `camus-cli -f` | Not a recovery tool for large data |
| Process crash or restart | WAL replay, automatic | Nothing to do |

Not recoverable by any in-engine tool: a dropped column, a dropped branch, a
table dropped inside a branch, a `FORCE` drop, and a reclaimed orphan. Only a
physical backup or a dump can bring those back.

Read [references/recovery-playbook.md](references/recovery-playbook.md) for the
step-by-step recovery recipes and the retention settings.

## Workflow: a safe migration

Follow this checklist for any schema change on a database with data.

1. **Inspect the current state.**
   ```sql
   SHOW CREATE TABLE orders;
   SHOW INDEXES FROM orders;
   SELECT COUNT(*) FROM orders;
   SHOW BRANCHES FROM prod;
   ```
2. **Check the preconditions for a branch.** The source must have no schema
   change in flight. It must have no foreign key still being added. Otherwise
   branch creation fails with `CADB0400`.
3. **Create a rehearsal branch.** It copies no rows. It reads the source
   snapshot and writes into its own overlay.
   ```sql
   CREATE DATABASE prod_migration_test BRANCH FROM prod;
   USE prod_migration_test;
   ```
   `USE` is a `camus-cli` command, not server SQL. Other clients select the
   database in the connection string.
4. **Run the migration script on the branch.** Use the same file you will run
   on the real database.
   ```bash
   camus-cli prod_migration_test -f migration.sql
   ```
   `-f` stops at the first failing statement and exits with status `1`.
   Earlier statements stay applied. Each DDL statement commits on its own.
5. **Verify on the branch.** Check the new schema, row counts, constraints, and
   key query plans.
   ```sql
   SHOW CREATE TABLE orders;
   SHOW INDEXES FROM orders;
   EXPLAIN (ANALYZE) SELECT id, status FROM orders WHERE status = "paid";
   ```
6. **If the run fails,** drop the branch and create a fresh one. Fix the script
   and repeat from step 3. Do not patch a half-migrated branch.
7. **Drop the branch** once it passed. Ask the user first. Drop descendants
   before parents (`SHOW BRANCHES FROM prod_migration_test`).
   ```sql
   DROP DATABASE prod_migration_test;
   ```
   A live branch holds the source's history from reclamation. Do not leave
   rehearsal branches around.
8. **Prepare a rollback anchor on the real database.** Note the UTC time. For a
   large or destructive change, also confirm a recent physical backup exists
   (`backup list` in `camus-cli`).
9. **Apply to the real database** after the user confirms. Use the same file.
10. **Verify the real database** with the checks from step 5. Run `ANALYZE` on
    tables whose shape or contents changed a lot.

Branching is an alpha feature. Its APIs and storage formats can change between
versions. Details: [references/branching.md](references/branching.md).

## Key rules for online DDL

CamusDB stages a new column or index through `DeleteOnly`, `WriteOnly`, then
`Public`. It backfills existing rows before the element becomes `Public`. The
backfill is checkpointed and resumes after a restart or a leader change. In a
cluster, every DDL statement goes through the schema leader and waits for live
nodes to converge before the next stage.

Supported `ALTER TABLE` forms (from the docs):

```sql
ALTER TABLE robots ADD COLUMN model STRING NULL;
ALTER TABLE robots DROP COLUMN model;
ALTER TABLE robots ADD PRIMARY KEY (id);
ALTER TABLE robots DROP PRIMARY KEY;
ALTER TABLE robots RENAME TO machines;
ALTER TABLE machines RENAME COLUMN name TO display_name;
ALTER TABLE robots ADD INDEX robots_kind_idx (kind) INCLUDE (name, year);
ALTER TABLE robots ADD UNIQUE INDEX robots_code_year_uq (code, year);
ALTER TABLE robots DROP INDEX robots_kind_idx;
ALTER TABLE robots RENAME INDEX robots_name_idx TO robots_display_name_idx;
ALTER TABLE products ADD CONSTRAINT positive_price CHECK (price > 0);
ALTER TABLE weather ADD CONSTRAINT weather_city_fk FOREIGN KEY (city) REFERENCES cities (name);
ALTER TABLE products DROP CONSTRAINT positive_price;
ALTER TABLE employees ALTER COLUMN name SET NOT NULL;
ALTER TABLE employees ALTER COLUMN name DROP NOT NULL;
ALTER TABLE docs ALTER COLUMN image SET STORAGE PLAIN;
ALTER TABLE docs REWRITE STORAGE;
ALTER TABLE sessions SET (ttl_expiration_expression = 'expires_at', ttl_job_cron = '@hourly');
```

Rules the agent must respect:

- **Renames are metadata only.** Renaming a table, column, or index moves no
  data. Foreign keys follow the rename.
- **A NOT NULL column on a non-empty table needs a default.** Without one, the
  statement fails with `CADB0301`. Otherwise, add it nullable, backfill in
  batches, then run `ALTER COLUMN ... SET NOT NULL`.
- **Prefer `ADD COLUMN ... DEFAULT` over a mass `UPDATE`.** The internal
  backfill is exempt from the mutation limit. A user `UPDATE` is not.
- **Validation scans run before commit.** `ADD CONSTRAINT ... CHECK`,
  `ADD CONSTRAINT ... FOREIGN KEY`, and `SET NOT NULL` fail if an existing row
  violates the rule. Run a query for violators first.
- **Dependent objects block drops.** Dropping a referenced table, column, or
  backing index fails with `CADB0530 DependentObjectsExist`. You cannot drop a
  column that an index uses (key or `INCLUDE`). Drop the index or constraint
  first.
- **No type change in place.** The docs show no `ALTER COLUMN ... TYPE`. To
  change a type, add a new column, backfill it, switch readers, then drop the
  old column.
- **To change an index's `INCLUDE` list,** drop the index and create it again.
- **A dropped column is gone for good.** Time travel reads through the current
  schema, so it cannot project a dropped column. Copy the column out first.
- **A transaction pins its schema.** A concurrent DDL can make its commit fail.
  Retry it from `BEGIN`.
- **`TRUNCATE` cannot run inside `BEGIN ... COMMIT`** (`CADB0538`).
- **Quiesce writers before adding a foreign key.** A long transaction that
  wrote an orphan row before the constraint existed can still commit it.

Full DDL rules, states, limits, and error codes:
[references/online-ddl.md](references/online-ddl.md).

## Key rules for data backfills

A user transaction may hold at most 20,000 mutations by default
(`max_mutations_per_transaction`). The limit fails with
`CADB0506 TransactionMutationLimitExceeded`. Retrying the same statement fails
again. Split the work.

| Operation | Mutations per row |
| --- | --- |
| Insert or delete, table with `K` secondary indexes | `2 + K` |
| Update that changes `M` indexed columns | `1 + 2M` |
| Each out-of-line large value written or deleted | `+1` |

Pick a batch size well below `20000 / cost_per_row`. Use a predicate that
excludes rows already done, and repeat until a run affects 0 rows:

```sql
UPDATE orders SET status = "archived"
WHERE status = "closed" AND closed_at < "2026-01-01"
LIMIT 4000;
```

`UPDATE` and `DELETE` require a `WHERE` clause and accept `LIMIT`. To empty a
whole table, use `TRUNCATE` instead of `DELETE`. It costs no mutations.

`INSERT ... SELECT` and the row load of `CREATE TABLE ... AS SELECT` are user
writes. They also obey the limit. For a large copy, run CTAS `WITH NO DATA`,
then `INSERT ... SELECT` in `WHERE` or `LIMIT` slices.

Raise the cap only with the user's consent, and only temporarily:

```sql
SET CLUSTER SETTING max_mutations_per_transaction = 40000;
RESET CLUSTER SETTING max_mutations_per_transaction;
```

## Key rules for recovery

- **Time travel is read-only and autocommit only.** It fails inside an
  explicit transaction with `CADB0409`. Feed it into `INSERT ... SELECT` or
  CTAS to write the old data into the present.
- **Use an absolute timestamp for multi-statement recovery.** A relative
  offset such as `'-2h'` moves with each statement.
- **An empty result can mean "history reclaimed".** There is no "too old"
  error. A CTAS or `INSERT ... SELECT` from an empty snapshot returns a warning.
- **After a `TRUNCATE`,** time travel before the cut fails with `CADB0537`.
  Use `SHOW ORPHAN TABLES` and `RELINK TO` instead.
- **`RELINK TO` always creates a new name.** The orphan id is an opaque string.
  Recovered tables come back without foreign keys. Add them again with
  `ALTER TABLE ... ADD CONSTRAINT`.

```sql
SHOW ORPHAN DATABASES;
CREATE DATABASE sales_recovered RELINK TO '7';

USE sales;
SHOW ORPHAN TABLES;
CREATE TABLE orders_recovered RELINK TO 'A0';
```

## Backups, dumps, and WAL

- **Physical backups** cover a whole node. They are off until you set
  `kahuna.backup_dir`. The API is HTTP only (`/v1/backups/*`, `/v1/restore`) on
  the server's HTTP port (`http_port`, default `5095`). `camus-cli` wraps every
  operation except restore as `backup ...`. A restore is offline into a fresh
  directory, followed by a restart against it. See
  [references/backup-and-restore.md](references/backup-and-restore.md).
- **`camus-dump`** writes replayable SQL. Use it for small databases, single
  tables, moving data between machines, and crossing storage revisions. Do not
  use it for disaster recovery of large data. See
  [references/dump-and-reimport.md](references/dump-and-reimport.md).
- **WAL recovery is automatic.** At startup, each partition replays its
  committed Raft log before it serves work. Uncommitted entries are dropped.
  Never delete or edit the `kv` or `wal` directories under `data_dir`.

## Common mistakes

- Running a migration straight on production without a branch rehearsal.
- Adding `FORCE` to a `DROP` "to clean up". It removes the safety net.
- Assuming time travel reaches days back. By default history follows
  `kahuna.pitr_window_seconds` (3600 seconds).
- Using `NOT IN (SELECT id FROM orders)` in a time-travel `INSERT ... SELECT`.
  Every subquery reads the same historical snapshot, not the live table. Copy
  the snapshot to a side table first, then diff against the live table.
- Retrying a `CADB0506` failure unchanged. It always fails again.
- Running a mass `UPDATE` to fill a new column instead of a column `DEFAULT`.
- Expecting `DROP DATABASE` on a branch to be recoverable. It is immediate.
- Trying to drop a parent database that has branches (`CADB0508`). Drop the
  leaves first.
- Leaving rehearsal branches alive. They pin source history and disk space.
- Pointing a restore at the live `data_dir`, or its `kv` or `wal` subdirectory
  (`CADB0707`).
- Using the backup API from a remote host with authentication off. The server
  accepts only loopback callers in that case.
- Restoring a physical backup with a different CamusDB storage revision. Use a
  logical dump to cross revisions.
- Forgetting that `camus-dump` does not export passwords or superuser status.

## References

- [references/online-ddl.md](references/online-ddl.md): read before writing
  any `ALTER TABLE` or index migration, or when a DDL error appears.
- [references/branching.md](references/branching.md): read when creating,
  inspecting, or dropping branches.
- [references/recovery-playbook.md](references/recovery-playbook.md): read
  after any accidental write, drop, or truncate.
- [references/backup-and-restore.md](references/backup-and-restore.md): read
  when enabling backups, taking one, or restoring.
- [references/dump-and-reimport.md](references/dump-and-reimport.md): read when
  moving data between servers or storage revisions.

Public docs:
https://camusdb.github.io/docs/sql-schema,
https://camusdb.github.io/docs/distributed-schema,
https://camusdb.github.io/docs/database-branching,
https://camusdb.github.io/docs/transaction-limits,
https://camusdb.github.io/docs/time-travel-reads,
https://camusdb.github.io/docs/insert-select-and-ctas,
https://camusdb.github.io/docs/recover-dropped-objects,
https://camusdb.github.io/docs/truncate-table,
https://camusdb.github.io/docs/backup-and-restore,
https://camusdb.github.io/docs/camus-dump,
https://camusdb.github.io/docs/logical-dump-and-reimport,
https://camusdb.github.io/docs/wal-recovery,
https://camusdb.github.io/docs/databases.
