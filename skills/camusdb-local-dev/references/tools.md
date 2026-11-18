# Developer tools reference

Sources: https://camusdb.github.io/docs/web-console,
https://camusdb.github.io/docs/operator-dashboard,
https://camusdb.github.io/docs/browser-playground,
https://camusdb.github.io/docs/workload-utility

## Web Console

A browser workspace: schema browser, Monaco SQL editor, result grid, row editing, CSV/JSON
export, `EXPLAIN` flow view, engine stats, variables, and backup controls.

Install it as a .NET tool (needs .NET 10):

```bash
dotnet tool install -g CamusDB.WebConsole
camusdb-webconsole --endpoint http://localhost:5095 --database demo
dotnet tool update -g CamusDB.WebConsole
```

- It listens on `http://127.0.0.1:5164` and opens the browser. Run a statement with
  Ctrl/Cmd+Enter; **Run script** (Alt+X) runs several statements in order.
- Options: `--endpoint` (default `http://localhost:5095`), `--database`, `--protocol rest|grpc`,
  `--user`, `--port` (`0` for a free port), `--no-browser`.
- There is no password option. Use Configure or `CamusDB__Password`.
- It binds to loopback. `--urls` overrides the address and turns off the automatic browser.

Or run the Docker image:

```bash
docker run --rm -p 127.0.0.1:8080:8080 \
  -e CamusDB__Endpoint=http://host.docker.internal:5095 \
  -e CamusDB__Database=demo \
  camusdb/camusdb-webconsole:latest
```

Open `http://localhost:8080`. On Linux, add `--add-host=host.docker.internal:host-gateway`,
or use the host LAN IP. The `127.0.0.1` binding keeps the port reachable only from the Docker host.

- The console has no sign-in of its own. Anyone who can reach it can use its configured CamusDB
  connection. Keep it on loopback for local work.
- Before you expose it, put an authenticated reverse proxy or VPN in front, turn on CamusDB
  authentication, and set `CamusDB__LockEndpoint=true` or `ConsoleLaunch__AllowedEndpoints`.

| Setting | Env var | Notes |
| --- | --- | --- |
| `Endpoint` | `CamusDB__Endpoint` | REST port for `rest`, gRPC port for `grpc` |
| `Database` | `CamusDB__Database` | Session database |
| `Protocol` | `CamusDB__Protocol` | `rest` (default) or `grpc` |
| `TimeoutSeconds` | `CamusDB__TimeoutSeconds` | Request timeout; default `30`, capped at `600` |
| `MaxRows` | `CamusDB__MaxRows` | Page size and grid and export row cap; default `1000`, capped at `100000` |
| `User` / `Password` | `CamusDB__User` / `CamusDB__Password` | Empty user means no auth |
| `AccessToken` | `CamusDB__AccessToken` | Token from elsewhere |
| `RequireAccessToken` | `CamusDB__RequireAccessToken` | Accept access tokens only; refuse user and password sign-in |
| `AllowInsecureCredentials` | `CamusDB__AllowInsecureCredentials` | Credentials to non-loopback `http://` |
| `LockEndpoint` | `CamusDB__LockEndpoint` | Stop sessions from repointing the endpoint |
| `BackupEndpoint` | `CamusDB__BackupEndpoint` | REST endpoint for backups when using gRPC or a pool |

- The Configure control in the app bar changes these for the current browser session.
- Exports are capped by `MaxRows`. Use `camus-dump` for a full logical dump.
- Right-click actions include drop table and drop database. The console asks for confirmation;
  confirm with the user too.
- Row edit and delete need a primary key.
- With auth on, Administration needs a superuser.

## Operator dashboard

Open the node HTTP port in a browser: `http://localhost:5095/`. It is read-only. It runs no DML
or DDL.

- It shows node health, load, engine metrics, databases, relations, backups, slow queries,
  configuration, and the cluster settings overlay.
- `dashboard_enabled: false` removes it (404). `dashboard_refresh_seconds` defaults to `2`.
- With auth off, it is loopback-only. The engine refuses others with
  "The dashboard over the network requires authentication_enabled to be turned on".
- With auth on, `/` redirects to `/SignIn`. The session cookie is `Secure`, so use HTTPS or loopback.
- Engine, Configuration, Slow queries, and Cluster panels need a superuser.
- Values are node-local. Turn on `slow_query_log_enabled` to fill the slow query panel.

## Browser playground

URL: https://camusdb.github.io/playground/. The tutorial page also embeds a SQL editor.

- The real engine, compiled to WebAssembly, runs inside the browser tab. No SQL or data leaves the tab.
- It starts four engines, with eight partitions and replication factor three.
- **+** adds a node and **−** removes one gracefully. It supports four to nine nodes.
- **Simulate node failure** stops a server. You can stop several servers. A partition that keeps
  a majority of its replicas elects a new leader within seconds, and the playground retries
  statements during the election. A partition without a majority cannot serve reads or commit
  writes; a statement that needs it fails after about 15 seconds. Other partitions stay available.
- **Restart node** brings back an empty server that catches up from surviving peers. Use
  **Reset data center** if the cluster cannot recover.
- A stopped server cannot run queries. The console offers to switch to a running server or to
  restart the stopped one.
- Adding or removing servers, including automatic scaling, waits until all servers are running
  voters. Three replicas tolerate one unavailable replica per partition.
- Data lives in memory. Reload or **Reset data center** starts over.
- Queries use the `playground` database, with a sample library of books and authors.
- Each statement autocommits. A script stops at the first error.
- There is no `USE` statement.
- No backups, no C# extensions, no inline C# compiler, and no features that need external files.

```sql
CREATE DATABASE lab;
CREATE DATABASE lab_copy BRANCH FROM lab;
SHOW BRANCHES FROM lab;
```

## Workload utility (CamusDB.Workload)

A measurement tool in the engine source tree. It seeds a deterministic dataset, drives reads
and writes, reconciles correctness, and writes artifacts. Run it from the engine repository.
Use `camus-cli workload` instead for a quick demo load.

```bash
dotnet run -c Release --project CamusDB.Workload -- init \
  --endpoint http://127.0.0.1:5096 --database workload --protocol grpc \
  --rows 100000 --payload-bytes 256 --batch 500

dotnet run -c Release --project CamusDB.Workload -- run \
  --endpoint http://127.0.0.1:5096 --database workload --protocol grpc \
  --output /tmp/camus-workload-run --mode open --target-ops 800 \
  --workers 64 --connections 8 --duration 5m --warmup 30s --drain 10s
```

Common options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--endpoint` | required | gRPC port for `--protocol grpc`; comma-separated pool allowed |
| `--database` | required | Workload database |
| `--protocol` | `grpc` | `grpc` or `rest` |
| `--seed` | `1847` | Deterministic seed |
| `--rows` | `100000` | Rows in `workload_accounts` |
| `--tables` | `1` | Spread over `workload_accounts_00`, `_01`, ...; same for `init` and `run` |
| `--payload-bytes` | `256` | Payload size per row |
| `--no-auto-prepare` | `false` | Adds `MaxAutoPrepare=0` |

Run options (selection):

| Option | Default | Meaning |
| --- | --- | --- |
| `--output` | required | Must not exist yet |
| `--mode` | `open` | `open` (target rate) or `closed` (saturation) |
| `--target-ops` | `800` | Open-loop ops per second |
| `--workers` | `64` | Concurrent workers |
| `--concurrency-sweep` | none | Closed-loop list, e.g. `1,8,16,32,64` |
| `--read-percent` / `--write-percent` | `60` / `40` | Must sum to 100 |
| `--duration` / `--warmup` / `--drain` | `5m` / `30s` / `10s` | Windows |
| `--locking` | `optimistic` | or `pessimistic` |
| `--isolation` | `read_committed` | or `serializable` |
| `--workload` | `accounts` | `accounts`, `bank`, `fanout`, `append`, `rw`, `predicate`, `tokens` |
| `--txn-mode` | `explicit` | For `append`, `rw`, and `predicate`: `explicit`, `autocommit`, or `pipeline` |
| `--init-if-missing` | `false` | Seed before the run if absent |

Choose a write shape by what you want to check:

- `bank` and `fanout` check a conserved `SUM(balance)`. `fanout` needs `--tables` of 2 or more.
- `tokens` deletes a row and inserts its successor in one transaction, with `--tokens` rows
  (default `2000`). It checks each slot after the run.
- `append`, `rw`, and `predicate` record `history.edn` and `elle.json`. Run the Elle checker separately.
  They also read the final state and judge it against the ledger of attempted writes.
- `rw` and `predicate` use Elle's `rw-register` checker. `append` uses `list-append`.

```bash
dotnet run -c Release --project CamusDB.Workload -- run \
  --endpoint http://localhost:5096 --protocol grpc --database workload \
  --workload rw --rw-access join --isolation serializable --duration 60s \
  --mode closed --workers 8 --output runs/rw-join
```

Other verbs:

```bash
dotnet run -c Release --project CamusDB.Workload -- compare \
  --baseline /tmp/base --candidate /tmp/cand --allow workers \
  --require-ratio 1.10 --p99-budget-ms 250 --output /tmp/cmp
dotnet run -c Release --project CamusDB.Workload -- baseline --runs /tmp/r1,/tmp/r2,/tmp/r3 --output /tmp/base
dotnet run -c Release --project CamusDB.Workload -- report --output /tmp/run --metrics /tmp/server-metrics.txt
dotnet run -c Release --project CamusDB.Workload -- cleanup \
  --endpoint http://127.0.0.1:5096 --database workload --protocol grpc --confirm workload
```

- `compare` exits `0` pass, `1` gate missed, `3` not comparable.
- `cleanup` drops the workload database. Confirm with the user first.
- Artifacts: `manifest.json`, `summary.json`, `summary.md`, `intervals.csv`, `errors.json`,
  `reconciliation.json`, `run-meta.json`, `client-resources.json`.
- The process exits above zero when the run is invalid or reconciliation fails.
- Do not benchmark with `memory_profile: dev`. Compare only runs with matching settings.
