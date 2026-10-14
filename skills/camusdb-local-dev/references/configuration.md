# Configuration reference for development

Sources:
https://camusdb.github.io/docs/configuration,
https://camusdb.github.io/docs/runtime-cluster-settings,
https://camusdb.github.io/docs/sql-authentication,
https://camusdb.github.io/docs/show-variables

## Files

- `camusdb init` writes `~/.camusdb/config.yml` (Windows: `%APPDATA%\camusdb\config.yml`).
- `camusdb init` refuses to overwrite an existing file. The engine source accepts `--force`
  to overwrite. Confirm with the user first; it replaces their settings.
- `CAMUS_HOME` moves the user config to `$CAMUS_HOME/config.yml` and data to `$CAMUS_HOME/data`.

Discovery order, first match wins:

1. `--config <path>`
2. `CAMUS_CONFIG_PATH`
3. `./camusdb.yml`
4. `./Config/config.yml`
5. `~/.camusdb/config.yml` (Windows: `%APPDATA%\camusdb\config.yml`)
6. Built-in defaults

A missing file named by `--config` or `CAMUS_CONFIG_PATH` fails startup.

## Default data directory

When nothing sets `data_dir`, the engine uses, in order:

1. `$CAMUS_HOME/data`
2. `$XDG_DATA_HOME/camusdb`
3. `~/.local/share/camusdb` (Windows: `%LOCALAPPDATA%\camusdb`)

`camusdb init` writes that resolved path into `data_dir`. The Docker image uses `/data`.
Note: the configuration docs table says `~/.camusdb/data`. The engine source and the
tutorial say `~/.local/share/camusdb`. Trust the startup banner line `Data directory: ...`.

## Precedence

Highest first:

1. Cluster setting overlay (`SET CLUSTER SETTING`, `runtime` keys only)
2. CLI flag (only when passed explicitly)
3. Environment variable
4. `config.yml`
5. Built-in default

## Keys and flags a developer touches

| YAML key | CLI flag | Default |
| --- | --- | --- |
| `data_dir` | `--data-dir` | see above |
| `mode` | `--mode` | `standalone` |
| `memory_profile` | `--memory-profile` | `prod` |
| `node_name` | `--raft-nodename` | `""` (machine name in cluster mode) |
| `raft_node_id` | `--raft-nodeid` | `1` |
| `raft_host` | `--raft-host` | `localhost` |
| `raft_port` | `--raft-port` | `7070` |
| `initial_partitions` | `--initial-cluster-partitions` | `1` standalone, `3` cluster |
| `peers` | `--initial-cluster` | `[]` |
| `http_peers` | `--http-peers` | `[]` |
| `join_existing` | `--join-existing` | `false` |
| `http_port` | `--http-port` | `5095` |
| `https_port` | `--https-port` | `7141` |
| `https_certificate` | `--https-certificate` | `""` |
| `raft_certificate` | `--raft-certificate` | `""` |
| `grpc_enabled` | (none) | `true` |
| `grpc_port` | (none) | `5096` |
| `grpc_certificate` | (none) | `""`, falls back to `raft_certificate` |
| `grpc_tls_enabled` | (none) | `true` |
| `require_tls_when_auth_enabled` | `--require-tls-when-auth-enabled` | `true` |
| `dashboard_enabled` | (none) | `true` |
| `dashboard_refresh_seconds` | (none) | `2` |
| `default_isolation_level` | (none) | `serializable` |
| `default_transaction_locking` | (none) | `pessimistic` |
| `key_range_sharding` | (none; env `CAMUS_KEY_RANGE_SHARDING`) | `false` |
| `slow_query_log_enabled` | (none) | `false` |
| `slow_query_log_threshold_ms` | (none) | `1000` |
| `engine_metrics_enabled` | (none) | `true` |
| `query_result_cache_enabled` | (none) | `true` |
| `spill_enabled` | (none) | `false` |
| `kahuna.*` | (none) | baseline for the mode; unknown keys fail startup |

The full table, with every tuning key, is at https://camusdb.github.io/docs/configuration.

## Notes on specific keys

- `memory_profile: dev` fixes caches at about 96 MiB on any machine. `prod` sizes them to
  about 16% of memory. `dev` is slower once the working set exceeds the cache.
- `key_range_sharding` is restart-scoped. `CAMUS_KEY_RANGE_SHARDING` accepts `true` or `1`.
- With no gRPC or Raft certificate, gRPC is plaintext HTTP/2. That suits local development.
- `grpc_tls_enabled: false` forces plaintext gRPC even when a certificate is set.
- `http3_enabled: true` needs `https_certificate` and a QUIC library. It is off by default.
- Other environment variables the server reads: `CAMUS_THREADPOOL_MIN_THREADS`,
  `CAMUS_LOG_LEVEL`, `CAMUS_LOG_LEVEL_KAHUNA`, `CAMUS_LOG_LEVEL_KOMMANDER`,
  `CAMUS_LOG_LEVEL_GRPC`, `CAMUS_LOG_FILTERS`. `DOTNET_gcServer=0` is a .NET runtime setting.

## Inspect the resolved configuration

```sql
SHOW VARIABLES;
SHOW VARIABLES LIKE 'slow_query_%';
```

Columns: `variable`, `value`, `type`, `default`, `source`, `mutability`, `scope`.
`source` is `default`, `config`, `env`, `cli`, or `cluster`. Secrets render as `********`.
The `LIKE` match is case-sensitive; names are lowercase.

## Runtime cluster settings

```sql
SET CLUSTER SETTING default_isolation_level = read_committed;
SET CLUSTER SETTING query_tracing_enabled = true;
SHOW CLUSTER SETTINGS;
SHOW CLUSTER SETTINGS LIKE 'ttl_%';
RESET CLUSTER SETTING default_isolation_level;
```

- Only `mutability = runtime` keys accept a change.
- Values use `config.yml` spelling: lowercase booleans, plain numbers, enum tokens with
  underscores and no quotes, durations in whole milliseconds.
- Any node accepts the statement. It commits through Raft and applies everywhere.
- Superuser only while auth is on (`CADB0517` otherwise). Anyone may run it while auth is off.

| Mistake | Error |
| --- | --- |
| Unknown key | `CADB0400` unknown cluster setting |
| `restart` key, e.g. `data_dir` or `kahuna.*` | `CADB0400` restart-bound |
| Value of the wrong type | `CADB0400`, names the key |
| Cross-field invariant broken | `CADB0600` `InvalidConfig` |

## Authentication environment

| Variable | Required | Meaning |
| --- | --- | --- |
| `CAMUSDB_AUTH_ENABLED` | yes | `true` enables auth. Any other value keeps it off. |
| `CAMUSDB_AUTH_TOKEN_KEY` | with auth | At least 32 bytes. Same on every node. |
| `CAMUSDB_BOOTSTRAP_USER` | first start | First superuser, used only while no user exists. |
| `CAMUSDB_BOOTSTRAP_PASSWORD` | first start | Its initial password. |
| `CAMUSDB_NODE_SECRET` | cluster with auth | At least 32 bytes when set. Same on every node. |

- There is no default user and no default password.
- Auth secrets are read from the environment only. Do not put them in YAML.
- `require_tls_when_auth_enabled` is not a secret. It belongs in YAML or on the CLI.

### Auth with the Docker image

The server exempts only loopback callers from the TLS rule. It checks the client IP that it
sees. A request through a Docker-published port usually arrives from a bridge address, not
loopback. Expect `CADB0519` over plain `http://` to a container with auth on.

Options, best first:

1. Run the node with the dotnet tool for auth tests. `localhost` is then real loopback.
2. Serve HTTPS with a certificate (`https_certificate`, port `7141`).
3. For a local-only container, disable the TLS check through a mounted config file. Bind the
   port to `127.0.0.1`. Never do this for a node that other clients can reach.

```bash
cat > ./camus-auth.yml <<'YAML'
require_tls_when_auth_enabled: false
YAML

docker run --rm \
  -p 127.0.0.1:5095:5095 -p 127.0.0.1:5096:5096 \
  -v camus-data:/data \
  -v "$PWD/camus-auth.yml:/etc/camusdb/config.yml:ro" \
  -e CAMUS_CONFIG_PATH=/etc/camusdb/config.yml \
  -e CAMUSDB_AUTH_ENABLED=true \
  -e CAMUSDB_AUTH_TOKEN_KEY="$CAMUSDB_AUTH_TOKEN_KEY" \
  -e CAMUSDB_BOOTSTRAP_USER=admin \
  -e CAMUSDB_BOOTSTRAP_PASSWORD="$CAMUSDB_BOOTSTRAP_PASSWORD" \
  camusdb/camusdb:latest
```

Keep `CAMUSDB_AUTH_TOKEN_KEY` stable across restarts of the same data volume.

### Login over HTTP

```http
POST /login
Content-Type: application/json

{ "user": "admin", "password": "secret" }
```

The response holds `token`, `expiresAtUnixMs`, and `expiresInSeconds`. Send
`Authorization: Bearer <token>` on later requests. `POST /logout` revokes it. gRPC clients
use the `CamusAuth` service and the `authorization` metadata key.

### Privileges

`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`, `DROP`, `ALTER`, `INDEX`, `CREATE`,
`ALL PRIVILEGES`. Scopes: `*.*`, `db.*`, `db.table`.

- A read needs `SELECT` on every table it touches, including joins and subqueries.
- A write with `RETURNING` also needs `SELECT`.
- `TRUNCATE` needs `DELETE` and `DROP`.
- Database lifecycle DDL and user management need a superuser.
- `GRANT` cannot create a superuser. Only the bootstrap does.
- Grant changes apply within the 1-second authorization cache TTL. `FLUSH PRIVILEGES` is rarely needed.

## Validation errors

Startup fails with `InvalidConfig` for: unknown `mode`, unknown `memory_profile`, a port
outside 1 to 65535, `http_peers` count different from `peers` count, an unknown `kahuna`
key, and other invariant breaks listed in the configuration docs.
