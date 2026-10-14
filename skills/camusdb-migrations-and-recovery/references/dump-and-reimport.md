# camus-dump and logical reimport

Sources: https://camusdb.github.io/docs/camus-dump,
https://camusdb.github.io/docs/logical-dump-and-reimport,
https://camusdb.github.io/docs/camus-cli

## When to use it

Use `camus-dump` when you need readable SQL you can carry elsewhere, and the
data is small enough to replay quickly:

- moving a database or table to another machine or cluster
- crossing a CamusDB storage revision
- exporting one table, or a filtered subset
- a reviewable SQL snapshot

Do not use it for disaster recovery of tens of gigabytes or more. Every row goes
through SQL as text, and the replay re-checks constraints and rebuilds indexes.
Use a physical backup for that.

## Install

```bash
dotnet tool install --global CamusDB.Dump
```

Requires the .NET 10 SDK. The executable is `camus-dump`.

## Connection

Defaults: gRPC on `http://localhost:5096`, database `test`.

| Option | Meaning |
| --- | --- |
| `-c`, `--connection-source` | Full connection string. |
| `-e`, `--endpoint` | Endpoint or comma-separated pool. Default `http://localhost:5096`. |
| `-d`, `--database` | Database to dump. Default `test`. |
| `-A`, `--all-databases` | Dump every database. |
| `-X`, `--exclude-database` | With `-A`, skip these databases. |
| `--protocol` | `grpc` (default) or `rest`. The port must match the protocol. |
| `--timeout` | Per-statement timeout in seconds. Default `10`. |
| `-u`, `--user` / `-W`, `--ask-password` | Log in. Prefer `CAMUSDB_PASSWORD` or `-W` over `-p`. |
| `--access-token` | Use an existing bearer token. |

Pointing `--protocol rest` at the gRPC port fails with "an HTTP/1.x request was
sent to an HTTP/2 only endpoint". With authentication on outside loopback, use
`https://`. The dump user needs the relevant `SHOW` and `SELECT` privileges.

## Selecting and shaping output

| Option | Meaning |
| --- | --- |
| `-t`, `--table` / `-x`, `--exclude-table` | Include or skip tables. |
| `-w`, `--where` | Only rows matching this condition. |
| `--as-of` | Read every table at this instant (`-5m`, absolute UTC, or epoch ms). |
| `--no-as-of` | Read latest data, no cross-table consistency. |
| `--single-transaction` | One serializable snapshot at the present. Cannot combine with `--as-of`. |
| `-b`, `--batch` | Rows per `INSERT`. Default `100`. |
| `-o`, `--output` | Output file. `.gz` selects gzip. |
| `--output-directory` | One `<database>.sql` per database. Not with `-o`. |
| `-z`, `--compress` | `gzip` or `none`. |
| `--if-not-exists` | `CREATE TABLE IF NOT EXISTS`, for an existing schema. |
| `--add-drop-table` | `DROP TABLE IF EXISTS` before each `CREATE TABLE`. Destructive on replay. |
| `--create-database` | `CREATE DATABASE IF NOT EXISTS` + `USE`. Implied by `-A`. |
| `--defer-indexes` | Secondary indexes after data. |
| `--no-create-table`, `--no-data`, `--no-indexes` | Skip parts. |
| `--all-users`, `--users` | Also export accounts and grants. |
| `--strict` | Fail instead of writing `NULL` for NaN or infinity. |
| `--no-header`, `--no-progress` | Quieter output. |

By default the dump fixes one instant (one second behind the wall clock) and
reads every table `AS OF SYSTEM TIME` that instant. The header records it.
Only rows are time-traveled. Schema, indexes, and sequence positions are
current.

## Moving data between machines

```bash
# 1. Dump the source.
camus-dump -e http://src.internal:5096 -d shop -b 100 -o shop.sql

# 2. Count rows on the source for later comparison.
camus-cli -c "Endpoint=http://src.internal:5095;Database=shop" \
  -e "SELECT COUNT(*) FROM orders"

# 3. Create the target database, then replay.
camus-cli -c "Endpoint=http://dst.internal:5095" -e "CREATE DATABASE IF NOT EXISTS shop"
camus-cli -c "Endpoint=http://dst.internal:5095;Database=shop" -f shop.sql
```

- `-f` stops at the first failing statement. `INSERT`s are not idempotent. If
  a load fails partway, fix the cause, ask the user, drop the partly loaded
  database, and run the file again.
- A dump made with `--create-database` or `-A` contains `USE`. Only
  `camus-cli` understands `USE`; the server parser rejects it. For other
  clients, dump with `--output-directory` and name the target with `-d`.
- Compressed: `gunzip -c shop.sql.gz | camus-cli shop -f -`.
- A complete dump ends each section with `-- Dump completed` (unless
  `--no-header`). Check for it after streaming.

## Crossing a storage revision

A newer server opens its own revision directory under `data_dir`. It leaves the
old revision untouched and does not convert it. The user catalog lives in the
same revision, so users and grants start empty on the new revision.

1. **Record before the move** (as a superuser): `SHOW USERS`,
   `SHOW GRANTS FOR *`, `SHOW VIEWS` + `SHOW CREATE VIEW`,
   `SHOW MATERIALIZED VIEWS` + `SHOW CREATE MATERIALIZED VIEW`, and
   `SHOW VARIABLES` (keep non-default values).
2. **Dump with the old version:**

   ```bash
   CAMUSDB_PASSWORD=secret camus-dump -e https://db1.internal:5096 \
     --all-databases -u admin -b 100 --all-users \
     --output-directory /backup/camusdb-old/
   ```

3. **Start the new version** on the target `data_dir`. If authentication is on,
   bootstrap a superuser first.
4. **Load in this order:**
   1. Users (`users.sql`).
   2. Databases, tables, indexes, foreign keys, and rows.
   3. Views and materialized views. Refresh materialized views.
   4. Grants (`grants.sql`).
   5. Cluster settings with `SET CLUSTER SETTING`.
   6. `ANALYZE` for each table.

   ```bash
   camus-cli -c "Endpoint=http://db1.internal:5095;Database=test" -f /backup/camusdb-old/users.sql
   camus-cli -c "Endpoint=http://db1.internal:5095;Database=test" -f /backup/camusdb-old/shop.sql
   camus-cli -c "Endpoint=http://db1.internal:5095;Database=test" -f /backup/camusdb-old/grants.sql
   ```

5. **Set passwords.** Passwords are never exported. Run
   `ALTER USER name IDENTIFIED BY '...'` for each account. Superuser status is
   not exported either.
6. **Verify:** `SHOW DATABASES`, `SHOW TABLES`, `SHOW INDEXES FROM <table>`,
   `SELECT COUNT(*)` against the recorded counts, and key queries. Check
   descending indexes if the dump came from an older server.
7. **Take a fresh full physical backup.** Only then, with user approval, remove
   old revision directories from every node.

Do not set `kahuna.storage_revision` to an older value to reach old data.

## Batch size and limits

- Start with `-b 100`. One row per `INSERT` is the slowest shape.
- Each row costs at least two mutations, plus one per secondary index and one
  per out-of-line large value. Large batches can hit the 20,000-mutation limit.
- gRPC messages are much smaller than HTTP bodies. Use a smaller `--batch` for
  tables with large `STRING`, `BYTES`, or `ARRAY` values.
- `--defer-indexes` helps a table with one secondary index. With several
  indexes, each deferred build scans the table again. Keep the target out of
  service until all indexes exist.

## Foreign keys

The dump orders tables and rows so parents load first. Filters (`-t`, `-x`,
`-w`) can leave out a parent; the tool warns. Alternative for hard cases: load
tables without `FOREIGN KEY` clauses, load rows, then
`ALTER TABLE ... ADD CONSTRAINT` to validate.

A dumped branch becomes an independent database on reimport.
