---
name: camusdb-local-dev
description: Sets up CamusDB for local development and testing, and drives its developer tools. Covers CamusDB.Server (dotnet tool, Homebrew brew install camusdb/tap/camusdb, brew services, camusdb init, ~/.camusdb/config.yml), the camusdb/camusdb Docker image, the compose cluster (docker/local.yml), growing a one-node cluster, ports 5095/5096/7070/7141, --grpc-port, camus-cli (-c, -e, -f, --tui), the Web Console, operator dashboard, browser playground, config.yml keys (data_dir, memory_profile, key_range_sharding, kahuna.storage, Solarium, max_statement_bytes), SHOW VARIABLES, SET CLUSTER SETTING, authentication (CAMUSDB_AUTH_ENABLED, CREATE USER, CADB0516 to CADB0519), the MCP server (CAMUS_MCP_ENDPOINT, .mcp.json), load tests (camus-cli workload), per-PR databases with CREATE DATABASE ... BRANCH FROM, Testcontainers, .NET Aspire (AddCamusDB, AddCamusDBCluster), and the Kubernetes Operator. Use it when a developer installs, starts, configures, connects to, secures, or load-tests a local CamusDB node or cluster.
---

# CamusDB local development

This skill takes a developer from nothing to a working CamusDB for development and testing.
It covers the server, the local cluster, `camus-cli`, configuration, authentication, the MCP
server, load tests, and throwaway databases for each pull request. CamusDB is a distributed
SQL database on .NET. It runs as one standalone node by default.

## Safety rules

- Confirm with the user before any destructive or irreversible command. This includes
  `DROP DATABASE`, `DROP TABLE`, `TRUNCATE`, `docker compose down -v`, deleting a data
  directory, `camusdb init --force`, and the workload `cleanup` verb.
- A `DROP DATABASE` of a branch is immediate. `RELINK TO` cannot recover it.
- Prefer a branch for any experiment on real data: `CREATE DATABASE x BRANCH FROM y`.
- Never write passwords, `CAMUSDB_AUTH_TOKEN_KEY`, or tokens into a committed file.
- Never assume a database exists. CamusDB has no implicit database creation.

## Workflow

Follow these steps in order. Skip the optional ones the user does not need.

1. Pick the target. Use the decision table below.
2. Start the server. Confirm it answers (step 4 works).
3. Install `camus-cli`: `dotnet tool install --global CamusDB.SqlSh`.
4. Connect and create a database: `CREATE DATABASE IF NOT EXISTS app;`.
5. Apply the schema with `camus-cli app -f schema.sql`.
6. Optional: tune configuration. Check the result with `SHOW VARIABLES`.
7. Optional: enable authentication for a realistic environment.
8. Optional: connect an AI coding agent through the MCP server.
9. Optional: create a branch per pull request for tests.
10. Optional: run a load test with `camus-cli workload`.

| Need | Use |
| --- | --- |
| Try SQL with nothing installed | Browser playground: https://camusdb.github.io/playground/ |
| Daily development, one node | NuGet tool `CamusDB.Server`, or Homebrew `camusdb/tap/camusdb` |
| One node without .NET installed | Homebrew (runtime included) or Docker image `camusdb/camusdb:latest` |
| Raft, failover, multi-node behavior | `docker compose -f docker/local.yml up --build` in the engine repo |
| Develop an app against a 3-node cluster | Aspire `AddCamusDBCluster` (see `references/local-cluster.md`) |
| Grow from one node to three | Start in cluster mode (see `references/local-cluster.md`) |
| Fault injection and chaos evidence | Caraxes (see `references/local-cluster.md`) |
| A database per .NET test, from test code | Testcontainers (see "Other ways to run CamusDB") |
| CamusDB beside your services in an AppHost | .NET Aspire (see "Other ways to run CamusDB") |
| A self-managed cluster on Kubernetes | Kubernetes Operator (see "Other ways to run CamusDB") |

## Install and start a node

Choose one method. Each starts a standalone node with REST on port `5095` and gRPC on `5096`.

```bash
dotnet tool install --global CamusDB.Server && camusdb    # NuGet tool; needs the .NET 10 SDK
brew install camusdb/tap/camusdb && camusdb               # macOS or Linux; runtime included
brew services start camusdb                                # Homebrew background service
docker run --rm -p 5095:5095 -p 5096:5096 -v camus-data:/data --name camusdb camusdb/camusdb:latest
curl --fail http://localhost:5095/health                   # check the node
```

- With no configuration file, `camusdb` uses built-in defaults. `camusdb init` writes a starter
  `~/.camusdb/config.yml` (Windows: `%APPDATA%\camusdb\config.yml`). It refuses to overwrite a file.
- The Homebrew service has its own configuration and data under `$(brew --prefix)/var/camusdb`.
  It does not read the foreground command's user configuration.
- `CAMUS_HOME` relocates the user configuration and the default data directory.
- `camusdb --version` prints only the version line, so a script can read it.
- Do not edit `CamusDB/Config/config.yml` in a source checkout; a pull can replace it.

Read `references/installation.md` for updates, file locations, and logs.

## Other ways to run CamusDB

- Testcontainers: `CamusDB.Testcontainers` starts a standalone server in Docker from test code.
  `CamusDB.Testcontainers.Xunit` and `CamusDB.Testcontainers.XunitV3` add fixtures. Host ports are
  random, so read addresses after `StartAsync`. Choose an image that has the features under test.
  https://camusdb.github.io/docs/testcontainers
- .NET Aspire: `CamusDB.Aspire.Hosting` runs a standalone container (`AddCamusDB`) or a local cluster
  (`AddCamusDBCluster`, three nodes by default) from the AppHost, and creates the declared databases.
  `CamusDB.Aspire.Client` and `CamusDB.Aspire.EntityFrameworkCore` register the driver or an EF Core
  context in a service. https://camusdb.github.io/docs/aspire
- Kubernetes Operator: an early-stage operator, installed from a Helm chart, runs a `CamusDBCluster`
  resource. It needs TLS. Do not use it for local development. https://camusdb.github.io/docs/kubernetes-operator

## Ports

| Port | Use | Key / flag |
| --- | --- | --- |
| `5095` | HTTP REST API, operator dashboard | `http_port` / `--http-port` |
| `5096` | Client gRPC API | `grpc_port` / `--grpc-port` |
| `7141` | HTTPS, only when `https_certificate` is set | `https_port` / `--https-port` |
| `7070` | Raft, cluster mode | `raft_port` / `--raft-port` |

The local compose cluster remaps host ports. Read `references/local-cluster.md` before you
connect to it.

## Connect with camus-cli

```bash
camus-cli                     # tries gRPC on localhost:5096, then REST on localhost:5095
camus-cli app                 # same, with database "app" selected
camus-cli -c "Endpoint=http://localhost:5095;Database=app;Protocol=rest"
camus-cli app -e "SELECT COUNT(*) FROM users" --format json
camus-cli app -f schema.sql   # stops at the first error, exit status 1
camus-cli app --tui           # full-screen catalog, editor, and results
```

Key rules:

- `use <db>` and `source <file>` are shell commands, not server SQL. `use` never creates a database.
- End a query with `\G` for vertical output.
- `-f` and `-e` together run the file first, then the `-e` SQL.
- With `--format json` or `yaml`, only rows go to stdout. Messages go to stderr.
- `exit` and `use` are blocked while a transaction is open. `commit` or `rollback` first.
- Server-level statements work with no database selected: `CREATE DATABASE`, `SHOW DATABASES`,
  `SHOW TABLES FROM`, `CREATE USER`, `GRANT`, `SHOW BRANCHES FROM`, `SHOW ANCESTORS FROM`.

Read `references/camus-cli.md` for every flag, meta command, and environment variable.

## Configure the node

Discovery order, first match wins: `--config <path>`, `CAMUS_CONFIG_PATH`, `./camusdb.yml`,
`./Config/config.yml`, `~/.camusdb/config.yml`, built-in defaults. A missing file named by
`--config` or `CAMUS_CONFIG_PATH` fails startup.

Precedence, highest first: cluster setting overlay (runtime keys only), CLI flag, environment
variable, `config.yml`, built-in default.

Essential keys for development:

```yaml
data_dir: /Users/me/.local/share/camusdb   # give each node its own directory
mode: standalone                            # or cluster
memory_profile: dev                         # ~96 MiB of caches; default is prod
http_port: 5095
grpc_port: 5096
initial_partitions: 1
slow_query_log_enabled: true
# key_range_sharding: true                  # fixed when the cluster is created; leave unset for hash routing
```

- Use `memory_profile: dev` when the node shares a laptop with the app. It is not a server setting.
- Choose the routing mode before you create data. `key_range_sharding` is fixed when the cluster
  is created. Hash routing is the default. A node with no value follows the recorded mode. A node
  set to the other mode refuses to start with `CADB0600`. To change mode, dump and reimport into a
  new cluster.
- `key_range_sharding` has no CLI flag. Set it in YAML or with `CAMUS_KEY_RANGE_SHARDING=true`.
- `kahuna.storage` and `kahuna.wal_storage` accept `memory`, `sqlite`, `rocksdb`, and `solarium`.
  Choose them before you create data. Solarium and RocksDB do not read each other's files.
- Inline C# functions and triggers are off by default. `inline_functions_enabled` and
  `inline_triggers_enabled` turn them on (restart, same value on every node). Use the
  `camusdb-csharp-extensions` skill for the feature.
- Check what the node resolved. Do not reconstruct the merge by hand:

```sql
SHOW VARIABLES LIKE 'memory_%';
```

The `source` column shows `default`, `config`, `env`, `cli`, or `cluster`. Read
`references/configuration.md` for defaults, flags, and validation errors.

## Change settings at runtime

`SET CLUSTER SETTING` changes a `runtime` key on every node, with no restart. It also works in
standalone mode.

```sql
SHOW VARIABLES LIKE 'max_mutations_%';      -- check mutability = runtime first
SET CLUSTER SETTING max_mutations_per_transaction = 40000;
SHOW CLUSTER SETTINGS;
RESET CLUSTER SETTING max_mutations_per_transaction;
```

- A `restart` key, such as `data_dir` or any `kahuna.*` key, fails with `CADB0400`. Edit the file and restart.
- An invariant break across two fields fails with `CADB0600` `InvalidConfig`.
- `RESET` removes the overlay. Each node falls back to its own local value.
- `SHOW VARIABLES` is node-local. Run it on every node to confirm a fleet change.
- Per-request limits such as `max_statement_bytes` are runtime settings. A value of `0` or less
  disables one. Read `references/configuration.md` for the list and the error codes.

## Enable authentication

Authentication is off by default. Turn it on for a realistic environment. Set it only through
environment variables, never in `config.yml`.

```bash
export CAMUSDB_AUTH_ENABLED=true
export CAMUSDB_AUTH_TOKEN_KEY="$(openssl rand -hex 32)"     # at least 32 bytes; same on every node
export CAMUSDB_BOOTSTRAP_USER=admin                          # used only while no user exists
export CAMUSDB_BOOTSTRAP_PASSWORD="$(openssl rand -base64 24)"
export CAMUSDB_NODE_SECRET="$(openssl rand -hex 32)"         # cluster only
camusdb
```

Then, as the bootstrap superuser:

```bash
camus-cli -u admin            # prompts for the password without echo
```

```sql
CREATE DATABASE IF NOT EXISTS app;
CREATE USER app IDENTIFIED BY 'app-secret';
GRANT SELECT, INSERT, UPDATE, DELETE ON app.* TO app;
GRANT ALTER, INDEX, CREATE TABLE ON app.* TO app;   -- only if the app runs migrations
SHOW GRANTS FOR app;
```

- Startup fails with an empty user catalog and no bootstrap values.
- A node with auth on and at least one peer also needs `raft_mutual_tls_enabled: true` and
  `raft_peer_ca`. Otherwise it refuses to start. A one-node setup does not need them.
- Credentials over plaintext are refused (`CADB0519`), except from loopback.
- A token lives 15 minutes by default. Clients renew from `expiresAtUnixMs`.
- Prefer `CAMUS_PASSWORD` or the prompt to `-p secret` on a shared host.

| Code | Meaning | Fix |
| --- | --- | --- |
| `CADB0516` | Not authenticated: missing, wrong, expired, or revoked credentials | Log in again or fix the credentials |
| `CADB0517` | Authenticated, but a privilege is missing on a touched table | `GRANT` the privilege, including joined tables |
| `CADB0518` | Login rate limit, or the limit on concurrent password verifications | Wait, then retry |
| `CADB0519` | Credentials over plaintext while TLS is required | Use `https://`, or connect from loopback |

Read `references/configuration.md` for the Docker and TLS details of auth.

## Connect an AI coding agent (MCP server)

The CamusDB MCP server speaks MCP over stdio. The agent client starts it. Build it once:

```bash
git clone https://github.com/camusdb/camusdb-mcp
cd camusdb-mcp
dotnet build camusdb-mcp.sln   # binary: CamusDB.Mcp/bin/Debug/net10.0/CamusDB.Mcp
```

Register it in the client. This block is the generic form (Claude Code reads it from `.mcp.json`):

```json
{
  "mcpServers": {
    "camusdb": {
      "command": "/path/to/camusdb-mcp/CamusDB.Mcp/bin/Debug/net10.0/CamusDB.Mcp",
      "env": {
        "CAMUS_MCP_ENDPOINT": "http://localhost:5095",
        "CAMUS_MCP_DEFAULT_DATABASE": "app_agent"
      }
    }
  }
}
```

- `CAMUS_MCP_ENDPOINT` defaults to `http://localhost:5095`. Set it for any other node.
- Point the agent at a branch, not at the source database: `CREATE DATABASE app_agent BRANCH FROM app;`.
- With auth on, add `CAMUS_MCP_USER` and `CAMUS_MCP_PASSWORD`, and grant only `SELECT` for a read-only agent.
- `select_query` accepts only `SELECT`, `SHOW`, and `DESCRIBE`. It rejects row-locking reads such as
  `FOR UPDATE`. `create_database`, `create_table`, and `insert_rows` can write.
- Diagnostic tools such as `get_slow_queries` read server statistics. Most need a superuser with auth on.

Read `references/mcp-server.md` for Codex and Cursor setup, all variables, and the tool list.

## Per-PR ephemeral databases

A branch is a copy-on-write clone. It copies no rows at creation. Use one per pull request:

```bash
DB="pr_${PR_NUMBER}"
camus-cli -e "CREATE DATABASE IF NOT EXISTS ${DB} BRANCH FROM staging"
camus-cli "${DB}" -f migrations/latest.sql
# ... run the test suite against Database=${DB} ...
camus-cli -e "DROP DATABASE ${DB}"          # CI teardown; ask first when run by hand
```

- The source must have no schema change in flight. Otherwise `CADB0400`.
- Drop leaf branches first. A parent with live branches fails with `CADB0508`.
- A live branch holds back storage reclamation on its source. Drop branches when done.
- A transaction cannot span two databases, so it cannot span a branch and its parent.

## Load-test a node

Quick load from the shell (defaults: database `demo`, 64 workers, 60 seconds):

```bash
camus-cli workload init bank --database demo --rows 5000
camus-cli workload run bank --database demo --concurrency 8 --duration 120
```

Workloads are `bank`, `northwind`, `factory`, `tpcc`, and `tpcb`. For repeatable measurement with
artifacts, compare, and baselines, use `CamusDB.Workload` from the engine source tree. Read
`references/tools.md` for its verbs and flags.

## Other tools

- Web Console: `dotnet tool install -g CamusDB.WebConsole`, then
  `camusdb-webconsole --endpoint http://localhost:5095` (opens `http://127.0.0.1:5164`). A Docker image also exists.
- Operator dashboard: open `http://localhost:5095/` in a browser. It is read-only.
- Browser playground: real engine in WebAssembly, in memory. Queries use the `playground` database. It has no `USE`.

Read `references/tools.md` for details and limits.

## Common mistakes

- Using a database before `CREATE DATABASE`. `camus-cli app` and `use app` do not create it.
- Running a standalone node and the compose cluster together. Both want host port `5095`, and
  the cluster puts node `camus2` HTTP on host port `5096`, the standalone gRPC port.
- Pointing a `Protocol=grpc` connection at port `5095`. gRPC is `5096` on a standalone node.
- Listing a node's own `raft_host:raft_port` in `peers`. Startup fails with `InvalidConfig`. List the other nodes.
- Setting `key_range_sharding` to a different mode on an existing cluster. The node refuses to start.
- Putting `CAMUSDB_AUTH_*` values in `config.yml`. Auth reads them from the environment only.
- Changing `CAMUSDB_AUTH_TOKEN_KEY` between nodes. Every node needs the same value.
- Expecting the dashboard or `backup` commands to work remotely with auth off. Both are
  loopback-only then. A Docker-published port usually does not count as loopback.
- Trying `SET CLUSTER SETTING` on a `restart` key. Check `mutability` in `SHOW VARIABLES` first.
- Using `memory_profile: dev` for a benchmark. It caps caches at about 96 MiB.
- Forgetting to drop per-PR branches. Each one pins history on its source.
- Using an uppercase `LIKE` pattern with `SHOW VARIABLES`. Variable names are lowercase and the match is case-sensitive.
  `_` is a wildcard; write `LIKE 'ttl\_%'` to match a literal underscore.

## References

- `references/installation.md`: NuGet, Homebrew, and Docker install, update, file and log locations. Read before installing or upgrading a node.
- `references/camus-cli.md`: all camus-cli flags, meta commands, history, output formats. Read before scripting the CLI.
- `references/configuration.md`: discovery, precedence, key defaults, flags, storage backends, request limits, auth environment, Docker auth, validation errors. Read before editing `config.yml`.
- `references/mcp-server.md`: MCP build, environment variables, Claude Code / Codex / Cursor config, tools. Read before wiring an agent.
- `references/local-cluster.md`: Aspire cluster, compose cluster ports, manual cluster flags, cluster sizing, growing a one-node cluster, storage health, manual range split, leadership transfer, Caraxes. Read before running more than one node.
- `references/tools.md`: Web Console, operator dashboard, browser playground, workload utility. Read before using any of them.

Public docs:

- https://camusdb.github.io/docs/intro
- https://camusdb.github.io/docs/server-installation
- https://camusdb.github.io/docs/camus-cli
- https://camusdb.github.io/docs/configuration
- https://camusdb.github.io/docs/cluster
- https://camusdb.github.io/docs/growing-a-one-node-cluster
- https://camusdb.github.io/docs/key-range-sharding
- https://camusdb.github.io/docs/runtime-cluster-settings
- https://camusdb.github.io/docs/sql-authentication
- https://camusdb.github.io/docs/mcp-server
- https://camusdb.github.io/docs/database-branching
- https://camusdb.github.io/docs/workload-utility
- https://camusdb.github.io/docs/web-console
- https://camusdb.github.io/docs/operator-dashboard
- https://camusdb.github.io/docs/browser-playground
- https://camusdb.github.io/docs/testcontainers
- https://camusdb.github.io/docs/aspire
- https://camusdb.github.io/docs/kubernetes-operator
