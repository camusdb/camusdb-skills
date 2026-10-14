# Local cluster reference

Sources: https://camusdb.github.io/docs/cluster, https://camusdb.github.io/docs/caraxes,
and the engine repository `docker/` folder and `README.md`.

Cluster mode is an alpha feature. APIs and storage formats can change between versions.

## Three nodes with Docker Compose

Run from the root of a CamusDB source checkout:

```bash
docker compose -f docker/local.yml up --build
```

Host port mapping in `docker/local.yml`:

| Node | HTTP REST | gRPC | HTTPS | Raft |
| --- | --- | --- | --- | --- |
| `camus1` | `localhost:5095` | `localhost:6095` | `localhost:7141` | `7070` |
| `camus2` | `localhost:5096` | `localhost:6096` | `localhost:7142` | `7072` |
| `camus3` | `localhost:5097` | `localhost:6097` | `localhost:7143` | `7074` |

Inside each container, HTTP is `5095` and gRPC is `5096`. The docs table names only the
HTTP and Raft ports; the gRPC column comes from the engine README and the compose file.

Connect over REST with an explicit protocol. Host port `5096` is `camus2` HTTP here, not gRPC:

```bash
camus-cli -c "Endpoint=http://localhost:5095;Protocol=rest"
camus-cli -c "Endpoint=http://localhost:5095,http://localhost:5096,http://localhost:5097;Protocol=rest"
```

The image sets a development certificate as both HTTPS and Raft certificate. gRPC reuses the
Raft certificate, so gRPC on `6095`–`6097` uses TLS with a self-signed certificate. Prefer REST
for local tools.

Each node mounts its own volume (`camus1-data`, `camus2-data`, `camus3-data`). Stop with
`docker compose -f docker/local.yml down`. Adding `-v` deletes all cluster data; confirm first.

The cluster image reads `CAMUS_EXTRA_ARGS` and appends it to the server command line. Example
from the Dockerfile: `CAMUS_EXTRA_ARGS="--join-existing true"`.

## The standalone Docker image

`camusdb/camusdb` runs `--mode=standalone --data-dir=/data` with these entrypoint variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CAMUS_HTTP_PORT` | `5095` | Passed as `--http-port` |
| `CAMUS_DATA_DIR` | `/data` | Passed as `--data-dir` |
| `CAMUS_INITIAL_PARTITIONS` | `4` | Passed as `--initial-cluster-partitions` |
| `CAMUS_LOG_LEVEL` | `Information` | Log level; also `CAMUS_LOG_LEVEL_KAHUNA`, `_KOMMANDER`, `_GRPC`, `CAMUS_LOG_FILTERS` |

The image uses 4 partitions. The dotnet tool and `camusdb init` use 1 for standalone.

## A cluster node by hand

Each node needs `--mode=cluster`, a unique name, its Raft host and port, the partition count,
and the static peer list:

```bash
camusdb \
  --mode=cluster \
  --raft-nodename=camus-1 \
  --raft-host=192.168.1.10 \
  --raft-port=7070 \
  --http-port=5095 \
  --initial-cluster-partitions=3 \
  --initial-cluster 192.168.1.10:7070 192.168.1.11:7072 192.168.1.12:7074 \
  --http-peers 192.168.1.10:5095 192.168.1.11:5096 192.168.1.12:5097
```

The same in YAML:

```yaml
data_dir: /data/
mode: cluster
node_name: camus-1
raft_host: 192.168.1.10
raft_port: 7070
initial_partitions: 3
http_port: 5095
peers:
  - 192.168.1.10:7070
  - 192.168.1.11:7072
  - 192.168.1.12:7074
http_peers:
  - 192.168.1.10:5095
  - 192.168.1.11:5096
  - 192.168.1.12:5097
```

- Give each node its own persistent `data_dir`.
- `http_peers` must have the same count and order as `peers`, or startup fails with `InvalidConfig`.
- With auth on, every node needs the same `CAMUSDB_AUTH_TOKEN_KEY` and `CAMUSDB_NODE_SECRET`.

## Behavior to expect

- Every node exposes the database API. A write is routed to the partition leader.
- A partition with no majority stops commits. It does not accept split-brain writes.
- `SET CLUSTER SETTING` commits through Raft. A node that was down catches up on replay.
- `SHOW VARIABLES` and the dashboard are node-local. Check each node.

Graceful leadership handoff (send it to the current leader):

```http
POST /v1/cluster/transfer-leadership
Content-Type: application/json

{ "partitionId": 1, "targetEndpoint": "10.0.0.3:7072" }
```

`200` means the target became leader. `409` returns `status` and `reason`. With auth off, the
route is loopback-only. With auth on, it needs a superuser.

## Caraxes (optional)

Caraxes is the reliability, chaos, and capacity harness for CamusDB. It lives in
`https://github.com/camusdb/camusdb-caraxes`. Use it when you need evidence about a build under
faults, not for daily development. It needs the .NET 10 SDK, Docker with `docker compose`, and
a CamusDB checkout (`~/camusdb` by default). Run commands from the Caraxes repository root.

```bash
dotnet run --project Caraxes -- up --spec scenarios/cluster-3.yml
dotnet run --project Caraxes -- status --spec scenarios/cluster-3.yml
dotnet run --project Caraxes -- logs --spec scenarios/cluster-3.yml --node camus2
dotnet run --project Caraxes -- down --spec scenarios/cluster-3.yml      # removes volumes unless --keep-volumes
dotnet run --project Caraxes -- run --scenario scenarios/smoke-optimistic.yml
```

- Node `i` publishes REST on host port `15095 + i - 1` and gRPC on `16095 + i - 1`. A Caraxes
  cluster can therefore run beside a local node on `5095` and `5096`.
- `run` exits `0` on PASS, `1` on FAIL, and `2` for an invalid spec. CI can gate on it.
- Faults include `kill`, `stop`, `pause`, `partition`, `slow`, `loss`, `fill-disk`, `slow-disk`,
  and `remove-node`.
