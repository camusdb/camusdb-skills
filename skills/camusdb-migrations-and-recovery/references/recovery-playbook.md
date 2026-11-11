# Recovery playbook

Sources: https://camusdb.github.io/docs/time-travel-reads,
https://camusdb.github.io/docs/insert-select-and-ctas,
https://camusdb.github.io/docs/recover-dropped-objects,
https://camusdb.github.io/docs/truncate-table,
https://camusdb.github.io/docs/configuration,
https://camusdb.github.io/docs/cluster#check-storage-health,
https://camusdb.github.io/docs/wal-recovery

Before any recovery: stop the process that caused the damage. Then ask the user
to confirm the target time, the affected tables, and the recovery name.

## Retention windows

| Mechanism | Setting | Default | Notes |
| --- | --- | --- | --- |
| MVCC history (time travel) | `kahuna.persistent_revision_retention_age_seconds` | Unset: follows `kahuna.pitr_window_seconds` (`3600`) | Must be at least the PITR window, or `0` to disable age pruning. A restart setting. |
| Per-key revision cap | `kahuna.persistent_revision_retention_count` | `0` (off) | Can trim history below the PITR window on hot keys. Prefer the age bound. |
| Orphans (dropped and truncated) | `orphan_retention_ms` | `604800000` (7 days) | `0` or below keeps orphans without limit. |
| Orphan reclaimer | `orphan_reclaim_interval_ms` | `300000` (5 minutes) | `0` or below disables the sweep. |
| Live WAL kept for PITR | `kahuna.pitr_window_seconds` | `3600` | Must be above `0`, at most `21600` (6 hours). |
| Backup chains | `kahuna.backup_retention_*` | `0` (no limit) | Each chain keeps its own recoverable range. |
| Branch hold lease | `branch_snapshot_hold_lease_ms` | `300000` | A live branch pins its source history. |

Use `SHOW VARIABLES LIKE 'orphan_%';` to see what a node resolved, and whether
a key can change at runtime. `kahuna.*` keys always need a restart.

Time travel beyond the history window does not error. It returns no rows. The
expiry shown by `SHOW ORPHAN ...` is when an orphan becomes eligible for
reclamation. Recovery works until it is actually reclaimed.

## Recipe 1: rows deleted or overwritten by mistake

Time travel reads current tables as they were at a past instant. It works for
updated rows and deleted rows. It does not work for a dropped column, or for a
table that was dropped and created again (new internal id).

1. Pick an absolute UTC instant just before the incident. Do not use a
   relative offset across several statements.
2. Look at the old data. This is read-only and autocommit only.

   ```sql
   SELECT id, customer, total
   FROM orders AS OF SYSTEM TIME '2026-08-07 14:00:00+00:00'
   WHERE customer = 'acme';
   ```

3. Rescue the old rows into a new side table.

   ```sql
   CREATE TABLE orders_rescue AS
   SELECT id, customer, total
   FROM orders AS OF SYSTEM TIME '2026-08-07 14:00:00+00:00'
   WHERE customer = 'acme';
   ```

   CTAS adds its own key column. Because the query already outputs `id`, the
   new key column is named `id2`. CTAS copies no index, constraint, or default.
   Its row load counts against `max_mutations_per_transaction`. For a large
   rescue, create the table `WITH NO DATA`, then copy slices with
   `INSERT ... SELECT ... AS OF SYSTEM TIME` and a range predicate per slice.

4. Check the response. A historical copy that read nothing returns `rows: 0`
   and a `warning`. That usually means the history was reclaimed.
5. Put the rows back. Read from the side table, not from a historical query.
   In a historical statement, every subquery also reads the past.

   ```sql
   INSERT INTO orders (id, customer, total)
   SELECT r.id, r.customer, r.total
   FROM orders_rescue r
   WHERE NOT EXISTS (SELECT * FROM orders o WHERE o.id = r.id)
   LIMIT 5000;
   ```

   Repeat until it inserts 0 rows. Size the batch by the mutation cost of the
   target table. A live source holds an exclusive range lock until commit under
   Serializable, and under key-range sharding at every isolation level. Under
   optimistic locking, every visited row is a commit dependency. Keep batches
   small on a busy source. A `GENERATED ALWAYS AS IDENTITY` column refuses
   supplied values unless the insert uses `OVERRIDING SYSTEM VALUE`.

6. For a wrong `UPDATE`, rescue the old values the same way. Then write
   per-row `UPDATE` statements from the side table, in batches:

   ```bash
   camus-cli shop -e "SELECT id, total FROM orders_rescue" --format json > old.json
   ```

   ```sql
   UPDATE orders SET total = 120.5 WHERE id = STR_ID('507f1f77bcf86cd799439011');
   ```

7. Verify counts and spot-check rows. Ask before dropping `orders_rescue`.

A historical copy is cheap. It takes no range locks, registers no commit
dependency, and blocks no writer. While it runs, the engine pins the revision
floor at the requested snapshot.

### Time-travel syntax

```sql
SELECT * FROM leaderboard AS OF SYSTEM TIME '-10s';
SELECT * FROM accounts AS OF SYSTEM TIME '2026-07-19T20:00:00Z' WHERE id = 9910;
SELECT * FROM accounts AS OF SYSTEM TIME 1721420000000;
SELECT c.name, o.total
FROM customers c JOIN orders o ON o.customer_id = c.id
AS OF SYSTEM TIME '-5m'
WHERE o.total > 100;
```

- Place the clause after the `FROM` source (after the joins), before `WHERE`.
- Values: a negative offset (`ms`, `s`, `m`, `h`, `d`), an absolute timestamp
  (UTC if no zone), epoch milliseconds, or a bound parameter.
- Not allowed: inside an explicit transaction, with `UPDATE`, `DELETE`, or
  `INSERT ... VALUES`, future times, and times at or before the Unix epoch.
  These fail with `CADB0409 InvalidAsOfSystemTime`.
- `INSERT ... SELECT` with a historical source does work inside an explicit
  transaction. Only the source reads the past.

## Recipe 2: a dropped table

Applies to tables in a root database, dropped without `FORCE`.

```sql
USE sales;
SHOW ORPHAN TABLES;
CREATE TABLE orders_recovered RELINK TO 'A0';
SELECT * FROM orders_recovered LIMIT 10;
```

- `SHOW ORPHAN TABLES` needs a current database. Columns: `id`, `kind`,
  `former_name`, `dropped_at`, `expires_at`.
- `kind` is `dropped table` for a drop and `retired contents` for a truncate.
- The orphan id is opaque. Copy it exactly.
- The recovered table keeps rows, indexes, primary key, unique and check
  constraints, and column definitions. It loses foreign keys. Add them back:

  ```sql
  ALTER TABLE orders_recovered ADD CONSTRAINT orders_customer_fk
    FOREIGN KEY (customer_id) REFERENCES customers (id);
  ```

- To get the original name back, confirm with the user, then rename:
  `ALTER TABLE orders_recovered RENAME TO orders;`. This fails with
  `CADB0013` if a table named `orders` exists.

## Recipe 3: a dropped database

Applies to root databases dropped without `FORCE`. Branches are not
recoverable.

```sql
SHOW ORPHAN DATABASES;
CREATE DATABASE sales_recovered RELINK TO '7';
USE sales_recovered;
SHOW TABLES;
```

- `SHOW ORPHAN DATABASES` needs no current database. Columns: `id`,
  `former_name`, `dropped_at`, `expires_at`.
- Ids are never reused. If a new `sales` already exists, the old one is still
  recoverable under another name, such as `sales_before_migration`.
- To restore the original name, confirm with the user, then
  `RENAME DATABASE sales_recovered TO sales;` once the name is free.

## Recipe 4: a truncated table

`TRUNCATE` keeps the old rows as `retired contents`.

```sql
SHOW ORPHAN TABLES;
CREATE TABLE orders_before_truncate RELINK TO 'A0';
```

- The live table is untouched. It keeps rows written after the truncate.
- Time travel to before the truncate fails with `CADB0537
  SnapshotPrecedesContentsGeneration`. Read the relinked table instead.
- Only the latest truncate cut is known. After several truncates, every
  snapshot before the last cut is refused.
- A dependent materialized view keeps stale rows until
  `REFRESH MATERIALIZED VIEW`.
- `TRUNCATE ... RESTART IDENTITY` resets only sequences owned by the table's
  identity or serial columns.
- After refilling, run `ANALYZE`. Old statistics are ignored.

## Recipe 5: outside every in-engine window

Use a physical backup. Restore offline into a new directory, start a server on
it, then copy the needed data back with `camus-dump` and `camus-cli`. See
[backup-and-restore.md](backup-and-restore.md) and
[dump-and-reimport.md](dump-and-reimport.md).

## Recipe 6: detected storage damage

With RocksDB or Solarium, a store that detects damage stops serving data. The
node gives up leadership and does not campaign. It does not rewrite the damaged
files.

- A read that reports `CADB0837 StorageCorrupted` failed. It did not find zero
  rows. It returns HTTP 500 or gRPC `DATA_LOSS`. Do not retry it as a
  transaction conflict.
- `GET /v1/cluster/health` returns HTTP 503 and `ready: false` when the node
  reports a store or partition storage fault. Read the body too: `storageFault`
  has the engine's message, and `partitionStorageFaults` lists hosted
  partitions. The messages can contain file paths. With authentication on,
  only a superuser sees these details.
- The endpoint does not scan for damage and does not repair it. A healthy
  endpoint does not prove that every stored byte is intact.
- Keep the damaged node out of service. Recover from the surviving replicas, or
  restore a [backup](backup-and-restore.md).

After a restart, `kahuna.raft_lost_acknowledged_entries_policy` decides what a
replica does when it finds that it lost log entries it acknowledged:

| Value | Behavior |
| --- | --- |
| `quarantine` (default) | The replica does not vote or campaign until its leader repairs it. With one replica there is no peer to repair from, so the partition stays leaderless and logs a Critical message. |
| `accept_and_log` | Accepts and logs the loss. This turns off the safety fence. It does not restore the entries. With one replica, the loss is permanent. |

A policy change needs a restart. An invalid value fails startup with
`InvalidConfig`. The policy does not repair damaged storage or restore a backup.

Backend limits:

- SQLite has no page checksums. Its WAL replay can stop at a damaged frame.
- SQLite Raft metadata uses `synchronous=NORMAL`, whatever `kahuna.wal_sync_writes` says.

To test recovery after a range split at a known point, call
`POST /v1/cluster/split-range` with `database`, `table`, optional `index`, and
`maxKeys` (default 1,000,000). The endpoint chooses the median key. With
authentication on, the caller must be a superuser. With it off, the endpoint
accepts loopback calls only. Automatic splits stay the normal way to handle growth. See
https://camusdb.github.io/docs/key-range-sharding#split-a-range-manually.

## Preventive habits

- Use plain `DROP` first. Use `FORCE` later, only when nobody needs the data.
- Raise `orphan_retention_ms` if a 7-day window is too short for your team.
- Keep a physical backup chain. A recoverable drop does not replace a backup.

## Error codes

| Code | Name | Meaning |
| --- | --- | --- |
| `CADB0409` | `InvalidAsOfSystemTime` | Bad timestamp form, future time, or used inside an explicit transaction. |
| `CADB0510` | `OrphanNotFound` | Unknown orphan id, already recovered, or already reclaimed. |
| `CADB0012` | `DatabaseAlreadyExists` | `CREATE DATABASE ... RELINK TO` target name is taken. |
| `CADB0013` | `TableAlreadyExists` | `CREATE TABLE ... RELINK TO` target name is taken. |
| `CADB0537` | `SnapshotPrecedesContentsGeneration` | Time travel before a truncate cut. |
| `CADB0538` | `StatementNotAllowedInTransaction` | `TRUNCATE` inside `BEGIN`. |
| `CADB0530` | `DependentObjectsExist` | `TRUNCATE` or `DROP` of a referenced parent. |
| `CADB0534` | `ConcurrentSchemaChange` | Lost a race with another truncate. Retryable. |
| `CADB0517` | `InsufficientPrivilege` | `TRUNCATE` needs both `DELETE` and `DROP` on the table. |
| `CADB0837` | `StorageCorrupted` | The store reports damage. Not retryable. Never read it as an empty result. |
