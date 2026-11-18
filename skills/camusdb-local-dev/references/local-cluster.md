# Local cluster reference

Sources: https://camusdb.github.io/docs/cluster, https://camusdb.github.io/docs/aspire,
https://camusdb.github.io/docs/growing-a-one-node-cluster,
https://camusdb.github.io/docs/key-range-sharding, https://camusdb.github.io/docs/caraxes,
and the engine repository `docker/` folder and `README.md`.

## When to use cluster mode

A standalone node is a single point of failure. Use cluster mode when one of these needs applies:

- No single point of failure. A three-node cluster keeps committing while one node is down.
- Durability on more than one machine. A commit is confirmed only after a majority holds it.
- Room to grow past one machine.

You do not need cluster mode for local development, tutorials, or quick tests. SQL and
transaction guarantees are the same in both modes, so the queries do not change. If you expect to
grow from one machine, start in cluster mode: a standalone node refuses joins, and its data needs
a dump and reimport.

## Three nodes with .NET Aspire

The easiest way to try a cluster or to develop an application against one. The AppHost starts
three containers, configures peers and Raft certificates, creates your database, and passes the
node endpoints to your service. It needs Docker and the .NET 10 SDK.

```csharp
var camus = builder.AddCamusDBCluster("camus", nodes: 3)
    .WithDataVolume();
var appDb = camus.AddDatabase("appdb");

builder.AddProject<Projects.ApiService>("api")
    .WithReference(appDb)
    .WaitFor(appDb);
```

- The dashboard shows a cluster resource and the containers `camus-1`, `camus-2`, and `camus-3`.
- Each node gets its own data volume. `WithDataBindMount(path)` uses one subfolder per node, such
  as `path/camus-1`.
- The AppHost generates a cluster CA and node certificates on each run. Raft uses TLS on the
  container network and does not publish its port. Client HTTP and gRPC stay plaintext, so this
  setup is for local development only. All nodes run on your machine.
- Readiness: each node checks `/v1/cluster/health`. On first startup, all nodes must be healthy
  before the AppHost creates databases. After that, the cluster resource stays healthy while a
  majority is healthy. Without a majority it is unhealthy. After all nodes stop, startup again
  needs every node.
- The connection string lists every node's endpoint. Cluster connection properties are `Uri`,
  `GrpcUri`, and `Database`. There is no single `Host`, `Port`, or `GrpcPort`.
- With gRPC, `BackupEndpoint` points to the first node's HTTP endpoint.
- `WithProtocol(CamusDBProtocol.Grpc)` and `WithAuthentication()` also work on clusters.
  Authentication shares the token key and node secret, and turns on mutual TLS between Raft peers.
- Nodes default to the `dev` memory profile. Prefer an odd node count. `nodes: 1` runs in cluster
  mode with no redundancy.

Apply common image or environment settings to every node. Keep them the same on all nodes:

```csharp
builder.AddCamusDBCluster("camus", nodes: 3)
    .ConfigureNodes(node => node
        .WithImageTag("0.17.0")
        .WithEnvironment("CAMUS_INITIAL_PARTITIONS", "8"));
```

The ready-to-run Notes example:

```bash
git clone https://github.com/camusdb/camusdb-aspire.git
cd camusdb-aspire
dotnet run --project examples/ClusterExample/ClusterExample.AppHost
```

Open the API's **Notes** URL after the resources are healthy. Stop `camus-3` and add a note: two
nodes still form a majority. Stop a second node and the cluster becomes unhealthy. Stop the
AppHost and wait for its containers to exit before you run it again, so two containers do not
open the same node's data. In the repository playground, `--Playground:ClusterNodes=3` starts a
cluster; the default `0` is standalone.

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

Inside each container, HTTP is `5095` and gRPC is `5096`. These ports come from the compose file
and the engine README. The site cluster page describes a three-terminal layout instead (see below).

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
from the Dockerfile: `CAMUS_EXTRA_ARGS="--join-existing true"`. An empty `CAMUS_INITIAL_CLUSTER` omits
`--initial-cluster`, so the image can start a one-node cluster.

## The standalone Docker image

`camusdb/camusdb` runs `--mode=standalone --data-dir=/data` with these entrypoint variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CAMUS_HTTP_PORT` | `5095` | Passed as `--http-port` |
| `CAMUS_DATA_DIR` | `/data` | Passed as `--data-dir` |
| `CAMUS_INITIAL_PARTITIONS` | `4` | Passed as `--initial-cluster-partitions` |
| `CAMUS_LOG_LEVEL` | `Information` | Log level; also `CAMUS_LOG_LEVEL_KAHUNA`, `_KOMMANDER`, `_GRPC`, `CAMUS_LOG_FILTERS` |

The dotnet tool and `camusdb init` use 1 partition for standalone. Note: the settings reference
says standalone mode always uses `1`. The image still passes `4`, and the Aspire docs set
`CAMUS_INITIAL_PARTITIONS` on a standalone container. Check the resolved value with
`SHOW VARIABLES LIKE 'initial_partitions';`.

## Three nodes on one machine, without Docker

Run three `CamusDB.Server` nodes, each in its own terminal. Nodes connect over TLS, so the Raft port
needs a certificate they trust. Without one, no partition elects a leader. Export the .NET
development certificate as a PFX with no password:

```bash
mkdir -p ~/camus-cluster && cd ~/camus-cluster
dotnet dev-certs https --trust
dotnet dev-certs https -ep localhost.pem --format PEM --no-password
openssl pkcs12 -export -in localhost.pem -inkey localhost.key -out localhost.pfx -passout pass:
```

Node 1. For nodes 2 and 3, change the name, ID, ports, data directory, and peer lists:

```bash
camusdb \
  --mode=cluster \
  --raft-nodename=camus1 --raft-nodeid=1 \
  --raft-host=localhost --raft-port=7070 \
  --raft-certificate=$HOME/camus-cluster/localhost.pfx \
  --http-port=5095 --grpc-port=6095 \
  --https-port=7141 --https-certificate=$HOME/camus-cluster/localhost.pfx \
  --data-dir=$HOME/camus-cluster/node1-data \
  --initial-cluster-partitions=3 \
  --initial-cluster localhost:7072 localhost:7074 \
  --http-peers localhost:5096 localhost:5097
```

| Node | HTTP | HTTPS | gRPC | Raft |
| --- | --- | --- | --- | --- |
| `camus1` | `5095` | `7141` | `6095` | `7070` |
| `camus2` | `5096` | `7142` | `6096` | `7072` |
| `camus3` | `5097` | `7143` | `6097` | `7074` |

- `--grpc-port` keeps each node's gRPC port apart from the others' HTTP ports.
- The gRPC port reuses the Raft certificate, so connect with `https://`:
  `Endpoint=https://localhost:6096;Database=shop;Protocol=grpc`.
- Use this layout for local testing only. Peer HTTP traffic is plaintext, and the nodes do not
  authenticate each other.

## A cluster node by hand

Each node needs `--mode=cluster`, a unique name, its Raft host and port, a Raft certificate, the
partition count, and the Raft address of every other node. List only the other nodes, never the
node itself. This is the first of three nodes, one per machine:

```bash
camusdb \
  --mode=cluster \
  --raft-nodename=camus-1 \
  --raft-host=192.168.1.10 \
  --raft-port=7070 \
  --raft-certificate=/etc/camusdb/node.pfx \
  --http-port=5095 \
  --initial-cluster-partitions=3 \
  --initial-cluster 192.168.1.11:7070 192.168.1.12:7070 \
  --http-peers 192.168.1.11:5095 192.168.1.12:5095
```

The same in YAML:

```yaml
data_dir: /data/
mode: cluster
node_name: camus-1
raft_host: 192.168.1.10
raft_port: 7070
raft_certificate: /etc/camusdb/node.pfx
initial_partitions: 3
http_port: 5095
peers:
  - 192.168.1.11:7070
  - 192.168.1.12:7070
http_peers:
  - 192.168.1.11:5095
  - 192.168.1.12:5095
```

- Give each node its own persistent `data_dir`.
- A `peers` entry equal to the node's own `raft_host:raft_port` fails startup with `InvalidConfig`.
- `http_peers` must have the same count and order as `peers`, or startup fails with `InvalidConfig`.
- The Raft certificate must name the node's Raft host, and every other node must trust it.
- With auth on, every node needs the same `CAMUSDB_AUTH_TOKEN_KEY` and `CAMUSDB_NODE_SECRET`. It
  also needs `raft_mutual_tls_enabled: true` and `raft_peer_ca`, or it refuses to start.

## Grow a one-node cluster

A standalone node refuses joins. To grow later, start the first node in cluster mode. To move
standalone data into a cluster, dump it and reimport it.

Choose these before the first boot. A later change has no effect or refuses the start:

- `initial_partitions`: fixed at the first boot. It defaults to `3` in cluster mode, but the sample
  `CamusDB/Config/config.yml` sets `1`. Check a copied file.
- `key_range_sharding`: the routing mode of the cluster.

```yaml
# First node. Do not set join_existing here.
mode: cluster
node_name: camus-node-1
raft_node_id: 1
raft_host: 10.0.0.1
raft_port: 7070
http_port: 5095
initial_partitions: 3
peers: []
```

```yaml
# Second node. peers is the seed list: one or more members of the running cluster.
mode: cluster
node_name: camus-node-2
raft_node_id: 2
raft_host: 10.0.0.2
raft_port: 7070
http_port: 5095
peers: [10.0.0.1:7070]
join_existing: true
```

- Add one node at a time. The node joins as a learner. Wait until it is a voter before you add the next.
- Do not stay on two voters. If one of them stops, the cluster cannot commit.
- Give every node the same `http_port`, so you do not need `http_peers`.
- These YAML files omit TLS. Add `raft_certificate` as in "A cluster node by hand".

Read https://camusdb.github.io/docs/growing-a-one-node-cluster for the full steps.

## Choose the number of nodes

Three nodes is the smallest cluster that survives one node failure. CamusDB has no configured
maximum. Size for the workload, and load test at the size you plan to run.

| Cluster | Replicas per partition | Nodes that can fail | Result |
| --- | --- | --- | --- |
| 3 nodes, default | 3 | 1 | Smallest layout with no single point of failure. |
| 5 nodes, default | 5 | 2 | Each node still stores all data. Each write waits for 3 of 5 replicas. |
| 5 or more nodes, `replication_factor: 3` | 3 | 1. If 2 fail together, only partitions with 2 replicas on those nodes stop. | Each new node takes a share of the partitions. |

- To survive more failures, add replicas. Keep the count odd: four tolerate one failure, like three.
- To add capacity, add nodes and set a replication factor below the node count. With the default
  full replication, a new node is one more copy of everything.

```yaml
kahuna:
  replication_factor: 3
  enable_placement_rebalancer: true
  zone: rack-1
```

- `replication_factor`: replicas of each partition. On a cluster with full replication, the
  rebalancer reduces existing partitions to that number. Without the rebalancer, they stay on
  every node.
- `enable_placement_rebalancer`: moves replicas to new nodes and repairs ranges that lost a
  replica. Off by default.
- `zone`: the rack or availability zone. CamusDB tries to spread each range's replicas across
  zones. This is best effort; check with `SHOW RANGES`. Use three or more zones.
- `initial_partitions` is fixed at creation and limits the spread. With factor 3, three partitions
  have nine replicas, so a tenth node has no data to host. One table lives on one partition by
  default, so more nodes do not help one busy table. See key-range sharding.
- Put nodes on separate machines, and preferably separate racks or zones.
- Give the application more than one way to reach the cluster: several endpoints in the
  connection string, or a load balancer. A request that ran on the failed node can still fail.
- New nodes join as learners, catch up, then become voters.

## Behavior to expect

- Every node exposes the database API. A write is routed to the partition leader.
- A partition without a majority stops committing. It does not accept a change that could
  conflict with another copy of the same data.
- `SET CLUSTER SETTING` commits through Raft. A node that was down catches up on replay.
- `SHOW VARIABLES` and the dashboard are node-local. Check each node.
- A starting node keeps waiting for partition leaders. A stop signal during that wait exits `0`. Any
  other startup failure logs the cause and exits `1`.

Graceful leadership handoff (send it to the current leader):

```http
POST /v1/cluster/transfer-leadership
Content-Type: application/json

{ "partitionId": 1, "targetEndpoint": "10.0.0.3:7072" }
```

- It is a graceful handoff, not a crash failover.
- `targetEndpoint` is the target's Raft address (`raft_host:raft_port`). The target must host the
  partition as a voter.
- `200` means the target became leader before the call returned. `409` returns `status` and
  `reason`, such as `NodeIsNotLeader`, `LeaderAlreadyElected`, or `Refused`.
- With auth off, the route is loopback-only. With auth on, it needs a superuser.

## Storage health

`GET /v1/cluster/health` keeps a node with detected storage damage out of service.

- A reported store or partition storage fault makes `ready` false, and the endpoint returns 503.
  Inspect the body as well as the status.
- `storageFault` holds the engine's fault message. `partitionStorageFaults` lists faults for
  hosted partitions. The messages can include file paths. With auth on, only superusers see them.
- A read that reports `CADB0837 StorageCorrupted` failed (HTTP 500, gRPC `DATA_LOSS`). It is not
  retryable and is never an empty result. Detection covers RocksDB and Solarium.
- The endpoint does not scan for integrity or repair storage.

## Split a range manually

Automatic splitting is the normal way. Use the manual split for a controlled point, such as a
recovery test.

```bash
curl -X POST http://localhost:5095/v1/cluster/split-range \
  -H 'Content-Type: application/json' \
  -d '{"database":"shop","table":"orders","maxKeys":1000000}'
```

- The endpoint picks a median key that is not already a range start. There is no caller split key.
- Omit `index` for rows, or name an index. `primary` and `<table>_pkey` name the primary index.
- `maxKeys` defaults to 1,000,000 and must be at least 2. A larger key space is refused.
- Hash-routed or unavailable range maps return `NoRange`.
- With auth on, the caller must be a superuser. With auth off, loopback only.
- 200 is a split, 409 a refused split, 400 invalid input. The body has `success`, `status`,
  `reason`, `keySpace`, `splitKey`, `keysSeen`, `descriptorsBefore`, `newPartitionId`,
  `leaderHint`, and `determinate`.
- On `NotLeader`, use `leaderHint`. If `determinate` is false, re-read `SHOW RANGES` and compare
  with `descriptorsBefore` before you try again.

## Caraxes chaos testing (optional)

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
- `run` exits `0` on PASS, `1` on FAIL or a harness failure, `2` for an invalid spec, and `130` on
  Ctrl-C. CI can gate on it.
- Faults include `kill`, `stop`, `pause`, `partition`, `slow`, `loss`, `fill-disk`, `slow-disk`,
  `clock-skew`, `leader-transfer`, `remove-node`, `backup`, `add-node`, and `sql`.
- Workload kinds are `accounts`, `bank`, `fanout`, `tokens`, and the Elle kinds `append`, `rw`, and
  `predicate`. Pick the kind by the failure you want to catch.
- The Elle kinds need the checker jar. Build it once with `tools/elle/build.sh`.
