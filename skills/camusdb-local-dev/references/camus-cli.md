# camus-cli reference

Source: https://camusdb.github.io/docs/camus-cli

`camus-cli` is the SQL shell for CamusDB. Install or update it:

```bash
dotnet tool install --global CamusDB.SqlSh
dotnet tool update --global CamusDB.SqlSh
```

## Syntax

```text
camus-cli [database] [options]
camus-cli workload <init|run> <bank|northwind|factory|tpcc|tpcb> [options]
```

## Connection

Without `-c`, the shell tries these in order:

```text
Endpoint=http://localhost:5096;Database=<database>;Protocol=grpc
Endpoint=http://localhost:5095;Database=<database>;Protocol=rest
```

- The connection string needs a valid absolute `Endpoint`, or a comma-separated pool.
- No `Protocol=` in `-c`: the shell tries gRPC, then REST, against the given endpoint.
- `Protocol=grpc` or `Protocol=rest`: the shell uses only that protocol. No fallback.
- No `Database=`: the shell starts with no selected database.
- The shell pings first, so a wrong endpoint fails at startup.

```bash
camus-cli -c "Endpoint=http://localhost:5095;Database=mydb;Protocol=rest"
camus-cli -c "Endpoint=http://localhost:5096;Database=mydb;Protocol=grpc"
camus-cli -c "Endpoint=http://node1:5096,http://node2:5096;Database=mydb;Protocol=grpc"
```

Extra connection-string keys shown in the docs:

| Key | Meaning |
| --- | --- |
| `User`, `Password` | Credentials. A flag overrides the same key. |
| `MaxAutoPrepare` | Number of auto-prepared statements kept. `0` turns auto-prepare off. |
| `AutoPrepareMinUsages` | Executions before a statement is prepared. |
| `BackupEndpoint` | HTTP endpoint for `backup` commands. Needed with `Protocol=grpc` or a node pool. |
| `BackupTimeout` | Backup request timeout in seconds. Default `300`. |

## Options

| Option | Description |
| --- | --- |
| `[database]` | Optional database name. |
| `-c`, `--connection-source` | Full connection string. |
| `-e`, `--execute` | Run one SQL string, or several separated by `;`, then exit. |
| `-f`, `--file` | Run a `.sql` file and exit. Stops at the first error, exit status `1`. `-f -` reads stdin. |
| `--format` | `table` (default), `json`, or `yaml`, for `-e` and `-f`. |
| `-u`, `--user` | User to authenticate as. |
| `-p`, `--password` | Password. Bare `-p`, or `-u` without `-p`, prompts without echo. |
| `--token` | Use an existing bearer token. The shell does not renew it. |
| `--allow-insecure-credentials` | Allow credentials to a non-local `http://` endpoint. Prints a warning. |
| `--no-history` | Do not load or save history. |
| `--tui` | Full-screen mode: catalog, editor, and results panes. |
| `--force-rich` | Force the rich editor. |
| `--diagnose-terminal` | Print terminal detection details and exit. |
| `-h`, `--help` / `-v`, `--version` | Help and version. |

| Environment variable | Default for |
| --- | --- |
| `CAMUS_USER` | `-u` |
| `CAMUS_PASSWORD` | `-p`. Keeps the password off the process command line. |
| `CAMUS_ACCESS_TOKEN` | `--token` |
| `CAMUS_FORCE_RICH` | `1`, `true`, or `yes` forces the rich editor. |

## Shell commands (not server SQL)

| Command | Description |
| --- | --- |
| `use <database>` | Switch database. The database must exist. Blocked inside a transaction. |
| `source <path> [--force]` | Stream a SQL file. Stops at the first error unless `--force`. |
| `show prepared [sql]` | Show auto-prepared statements. Alias `\prepared`. |
| `backup ...` | Backup administration (see below). |
| `clear` | Clear the screen. |
| `exit` / `quit` | Exit. Blocked inside a transaction. |

`use` and `source` also work inside `-e`, `-f`, and script files. A file may switch databases
with `use`, but not inside an open transaction.

## Scripts and output

```bash
camus-cli app -f schema.sql
cat schema.sql | camus-cli app -f -
camus-cli app -f seed.sql -e "select count(*) from users"     # file runs first
camus-cli app -e "SELECT * FROM users\G"                       # vertical output
camus-cli app -e "select id, name from users" --format json | jq '.[].name'
```

- The shell splits statements at `;`. It ignores `;` inside quotes, backticks, and comments.
- Files stream. A dump larger than RAM works.
- JSON: one array of objects per result set. YAML: one sequence per result set, separated by `---`.
- `NUMERIC` prints as a string. `BYTES` prints as base64. `NULL` stays null.
- An open transaction stops a `source` file even with `--force`.

## Transactions

```sql
begin;            -- or: start transaction;
commit;
rollback;
```

Only one transaction at a time. `Ctrl+C` rolls back an active transaction before exit.

## Full-screen mode (`--tui`)

| Key | Action |
| --- | --- |
| `Tab` / `Shift+Tab` | Move between panes |
| `F5` / `Ctrl+R` / `Shift+Enter` | Run every statement in the editor |
| `Esc` | Cancel the running query |
| `F1` | Toggle key list |
| `F2` | Toggle the 500-row display cap (not a SQL `LIMIT`) |
| `Ctrl+S` | Save editor text |
| `Ctrl+Q` | Quit |

`backup` commands do not run inside `--tui`.

## Authentication

```bash
camus-cli app -u app                         # prompt for password
CAMUS_USER=app CAMUS_PASSWORD=... camus-cli app
camus-cli app --token "$CAMUS_ACCESS_TOKEN"
```

- The shell refuses credentials to a non-local `http://` endpoint. Use `https://`.
- A statement with `IDENTIFIED ... BY '...'` is kept out of the on-disk history.

## Backup commands

```text
backup full | incremental <parent-id> | coordinated | list | chain <leaf-id> | gc preview | gc
```

- Backups are node-wide and need `kahuna.backup_dir`. Otherwise `CADB0700 BackupNotConfigured`.
- They need a superuser with auth on. With auth off, only a loopback caller may run them.
- For backup and restore workflows, see https://camusdb.github.io/docs/backup-and-restore.

## History

| Platform | Path |
| --- | --- |
| `XDG_STATE_HOME` set | `$XDG_STATE_HOME/camusdb/history.json` |
| Linux and macOS | `~/.config/camusdb/history.json` |
| Windows | `%LOCALAPPDATA%\camusdb\history.json` |

The TUI editor text persists as `query.sql` in the same directory.

## Workload subcommand

| Option | Default |
| --- | --- |
| `-c`, `--connection-source` | gRPC `localhost:5096`, then REST `localhost:5095` |
| `--database` | `demo` |
| `--rows` | `1000` (accounts for `bank`/`tpcb`, warehouses for `tpcc`) |
| `--concurrency` | `64` |
| `--duration` | `60` seconds |
| `--locking` | `optimistic` (or `pessimistic`) |
| `--isolation` | `serializable` (or `read-committed`) |
| `--no-prepare` | off |
| `-u`, `-p`, `--allow-insecure-credentials` | as above |

```bash
camus-cli workload init tpcc --database tpcc --rows 1
camus-cli workload run tpcc --database tpcc --concurrency 4 --duration 120
```
