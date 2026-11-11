---
name: camusdb-migrations-and-recovery
description: Plans and runs safe CamusDB schema changes and recovers lost data. Covers online DDL (ALTER TABLE, CREATE INDEX, DROP INDEX, DeleteOnly/WriteOnly/Public states, backfill), migration rehearsal on a branch (CREATE DATABASE ... BRANCH FROM, SHOW BRANCHES, SHOW ANCESTORS), batched backfills under the 20,000-mutation limit, AS OF SYSTEM TIME recovery, SHOW ORPHAN TABLES, SHOW ORPHAN DATABASES and RELINK TO, TRUNCATE recovery, physical backups and PITR (/v1/backups, /v1/restore, camus-cli backup, pitr_bootstrap_from), camus-dump, logical dump and reimport across storage revisions, Solarium and key_range_sharding, and WAL crash recovery. Use when writing or applying a migration, after an accidental DROP, DELETE, UPDATE or TRUNCATE, when setting up backups or retention, before a server upgrade, or when you see CADB0506, CADB0530, CADB0534, CADB0537, CADB0539, CADB0510, CADB0503, CADB0600, CADB0837 StorageCorrupted or a CADB07xx backup error.
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
  `ALTER TABLE ... DROP COLUMN`, `DROP INDEX` in any form, `RESET CLUSTER
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
| Move data to another machine, storage revision, storage engine, or routing mode | `camus-dump` + `camus-cli -f` | Not a recovery tool for large data |
| Add a node to a running cluster without a full snapshot | Restored backup + `pitr_bootstrap_from` | The chain's range, inside the leader's retention |
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
   branch creation fails with `CADB0400`. A branch create on the node that runs
   a staged change waits for that change first.
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

Branch details: [references/branching.md](references/branching.md).

## Key rules for online DDL

CamusDB stages a new column or index through `DeleteOnly`, `WriteOnly`, then
`Public`. It backfills existing rows before the element becomes `Public`. The
backfill is checkpointed and resumes after a restart or a leader change. In a
cluster, every DDL statement goes through the schema leader and waits for live
nodes that hold the database open before the next stage.

A batch that loses a row lock to a writer is retried without limit. The batch
halves on each loss. A table under sustained writes is still indexed or filled,
only more slowly. The caller of the statement waits for the whole backfill.

Supported `ALTER TABLE` and index forms (from the docs):

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
DROP INDEX robots_kind_idx ON robots;
DROP INDEX IF EXISTS robots.robots_kind_idx;
CREATE INDEX events_payload_gin ON events USING GIN (payload);
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
- **A transaction pins its schema.** A concurrent DDL can make a later statement
  or the commit fail with the retryable `CADB0502`. Retry it from `BEGIN`.
- **DDL on another table does not wait.** A staged change holds only its own
  table during a backfill. DDL on that table waits. `DROP DATABASE` and a branch
  create wait for every staged change.
- **A failed `ADD COLUMN` or `ADD CONSTRAINT` leaves nothing behind.** You can run
  the same statement again.
- **Check the schema before you rerun after a handover.** If the schema leader
  moves, the statement answers from what the new leader did. If that is still
  running after ten minutes, the statement fails and the change finishes in the
  background.
- **`CADB0099` on DDL is not safe to replay.** The entry may still commit. Read
  the schema first. `CADB0504` means nothing was committed, so rerun it.
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
then `INSERT ... SELECT` in `WHERE` or `LIMIT` slices. The source can be a
plain view.

A copy out of a busy table depends on the isolation level and locking mode:

- Serializable holds an exclusive range lock on the scanned range until commit.
  Under key-range sharding, this lock applies at every isolation level.
- Read Committed with pessimistic locking takes no lock.
- Optimistic locking makes every visited row a commit dependency. A bulk copy
  from a table under sustained writes then does not commit (`CADB0502`).

A source with `AS OF SYSTEM TIME` takes no lock and no dependency. Otherwise run
the copy pessimistically, or copy small slices.

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
- **Add a node to a running cluster from a backup** with a seeded join
  (`pitr_bootstrap_from`). A restored data root started alone loses its data to
  the leader's snapshot.
- **`camus-dump`** writes replayable SQL. Use it for small databases, single
  tables, moving data between machines, and crossing storage revisions. Do not
  use it for disaster recovery of large data. See
  [references/dump-and-reimport.md](references/dump-and-reimport.md).
- **WAL recovery is automatic.** At startup, each partition replays its
  committed Raft log before it serves work. Uncommitted entries are dropped.
  Never delete or edit the `kv` or `wal` directories under `data_dir`. Replay
  assumes intact data: it does not repair damaged files.
- **Detected storage damage** (`CADB0837 StorageCorrupted`) stops the store.
  It is not retryable and is never an empty result. `GET /v1/cluster/health`
  then returns HTTP 503. A replica that lost acknowledged log entries is
  quarantined by default (`kahuna.raft_lost_acknowledged_entries_policy`). See
  Recipe 6 in [references/recovery-playbook.md](references/recovery-playbook.md).

## Upgrade a server

An upgrade can change how stored objects behave. New `INT` and `SMALLINT`
columns enforce their range, but old ones do not. Stored `LIKE` patterns take
the new escape rules. Stored views and `CHECK`s keep their old operator
grouping. Rehearse the upgrade on a branch, inspect `SHOW CREATE TABLE` and
`SHOW CREATE VIEW`, and keep all nodes on one version when results must agree.
Read [references/upgrade-behavior.md](references/upgrade-behavior.md) first.

## Choices you cannot change in place

Each of these needs a logical dump and reimport. See
[references/dump-and-reimport.md](references/dump-and-reimport.md).

- **Routing mode.** A cluster records `key_range_sharding` once, when it is
  created. A node set to the other mode refuses to start (`CADB0600`). Reimport
  into a new cluster.
- **Storage engine.** Solarium does not read RocksDB files, and RocksDB does not
  read Solarium files.
- **Storage revision.** A newer revision does not read older files.
- **Standalone to cluster.** A standalone node refuses every join. Reimport into
  a cluster. To grow later, start one node in cluster mode.

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
  accepts only loopback callers then. Others get `CADB0517`.
- Dropping an index first when you replace it on a live table. Create the new
  one, verify the plan, then drop the old one.
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
- [references/upgrade-behavior.md](references/upgrade-behavior.md): read before
  a server upgrade, or when a stored view, `CHECK`, or column acts differently
  after one.

Public docs, under `https://camusdb.github.io/docs/` (append the page name):
`sql-schema`, `sql-indexes`, `distributed-schema`, `database-branching`,
`transaction-limits`, `time-travel-reads`, `insert-select-and-ctas`,
`recover-dropped-objects`, `truncate-table`, `backup-and-restore`, `camus-dump`,
`logical-dump-and-reimport`, `key-range-sharding`, `growing-a-one-node-cluster`,
`configuration`, `wal-recovery`, `cluster#check-storage-health`, `views`,
`databases`.
