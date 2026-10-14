# Distributed transactions and historical reads

Read this file when a transaction spans partitions or nodes, when you need to
reason about commit durability, or when you write an `AS OF SYSTEM TIME` query.

## Contents

- Two-phase commit
- HLC timestamps
- Durability and failure outcomes
- Node affinity of a transaction
- AS OF SYSTEM TIME syntax
- AS OF SYSTEM TIME rules
- Choosing between snapshot options

## Two-phase commit

A transaction that touches several partitions commits on all of them or on
none. The flow:

1. `BEGIN` asks Kahuna for an HLC transaction id and a coordinator handle.
2. Statements read, write, maintain indexes, and take locks. Each confirmed
   operation registers with the coordinator.
3. `COMMIT` validates the schema pins.
4. Kahuna validates read dependencies when needed.
5. Kahuna prepares the mutations on every participant, with a fresh commit
   timestamp.
6. If every participant prepares, Kahuna commits. If one fails, it rolls back.
7. The coordinator releases the locks and finalizes the session.

What you can rely on:

- A committed write never becomes partly visible across partitions.
- A conflicting write is reported as a failure or a retry. It is never merged.
- A serializable transaction keeps a stable snapshot (read-only) or lock
  semantics (read-write) across partitions.
- Internal retries are idempotent. Each operation carries an operation id.

What you still must do:

- Treat a failed explicit transaction as a failed unit. Replay it from `BEGIN`.
  The replay may observe different data.
- On `CADB0509`, resend the same finalize. Do not replay.

Under hash routing (the default), all rows and indexes of one table live on one
partition. A transaction across tables can still span partitions. Key-range
sharding splits a table across partitions, so more writes become distributed.

Distributed transaction support is an alpha feature. APIs and behavior can
change between versions.

## HLC timestamps

- Kahuna assigns each read-write transaction a Hybrid Logical Clock timestamp.
  A client never assigns one.
- An HLC timestamp has a logical wall-clock part `L` and a counter `C`. CamusDB
  prints it as `HLC(L:C)`.
- The commit timestamp is at least as new as the start timestamp and as every
  value the transaction modified or depended on.
- The order is logically consistent. It is not tied to real time. CamusDB has no
  externally consistent commit-wait guarantee.

## Durability and failure outcomes

| Event | Outcome |
| --- | --- |
| Failure before the durable commit decision | Not reported as committed. Replay from `BEGIN` if you still want the change. |
| Node restart after an acknowledged commit | CamusDB restores committed log entries and applies them to the KV store. |
| Partition leader change | The new leader continues from a Raft-safe log. |
| Coordinator lost after the commit decision | The decision-anchor partition drives the remaining participants to commit. |
| Finalize outcome not known within the budget | `CADB0509`. Resend the same finalize. |

CamusDB never exposes a half-committed SQL transaction.

## Node affinity of a transaction

- An explicit transaction lives in memory on the node that started it.
- Send every statement and the `COMMIT` or `ROLLBACK` to that node. Other nodes
  do not know the handle and reject it.
- An autocommit statement can go to any node.
- A transaction is not tied to a socket. A client can reconnect and continue
  with the same handle, on the same node.
- A transaction opened inside a gRPC `BatchExecute` stream belongs to that
  stream. It rolls back if the stream ends without a finalize.
- The .NET driver and TypeScript connector pin transaction commands to the
  transaction endpoint for you.

## AS OF SYSTEM TIME syntax

Write the clause after the `FROM` source, before `WHERE`, `GROUP BY`,
`ORDER BY`, `LIMIT`, and `OFFSET`:

```sql
SELECT *
FROM accounts AS OF SYSTEM TIME '-10s'
WHERE id = 9910;

SELECT c.name, o.total
FROM customers c
JOIN orders o ON o.customer_id = c.id
AS OF SYSTEM TIME '-5m'
WHERE o.total > 100;
```

Accepted values:

| Form | Example |
| --- | --- |
| Relative offset (must be negative) | `'-500ms'`, `'-10s'`, `'-2m'`, `'-1h'`, `'-1d'` |
| Absolute timestamp, UTC when no zone is given | `'2026-07-19T20:00:00Z'`, `'2026-07-19 20:00:00+00:00'` |
| Epoch milliseconds | `1721420000000` |
| Bound parameter | `@ts` (string, epoch milliseconds, or datetime) |

Every table, join, and subquery of the statement reads the same snapshot.

Copy historical rows into current tables:

```sql
CREATE TABLE orders_before_incident AS
SELECT customer, total
FROM orders AS OF SYSTEM TIME '-2h';

INSERT INTO orders (id, customer, total)
SELECT gen_id(), customer, total
FROM orders AS OF SYSTEM TIME '-2h'
WHERE customer = 'acme';
```

## AS OF SYSTEM TIME rules

- Supported for a read-only `SELECT` in autocommit.
- Rejected inside an explicit multi-statement transaction.
- Allowed as the source of `INSERT INTO ... SELECT` and `CREATE TABLE ... AS
  SELECT`. The writes target the current state.
- Not allowed directly on `UPDATE`, `DELETE`, or `INSERT ... VALUES`.
- Past instants only. A future time, or a time at or before the Unix epoch, is
  rejected.
- A timestamp older than retained history returns an empty result, not an error.
- A read before the latest `TRUNCATE` cut fails with `CADB0537`. Recover old
  rows with `CREATE TABLE ... RELINK TO`.

Errors:

| Code | Cause |
| --- | --- |
| `CADB0409` `InvalidAsOfSystemTime` | Malformed duration such as `'-10x'`, a positive offset such as `'+10s'`, a future timestamp, epoch milliseconds `<= 0`, a missing or incompatible parameter, or use inside an explicit transaction |
| `CADB0537` `SnapshotPrecedesContentsGeneration` | The snapshot precedes the current contents of a truncated table |

## Choosing between snapshot options

| Need | Use |
| --- | --- |
| One query over a past state | `AS OF SYSTEM TIME` in autocommit |
| Several queries over one consistent current snapshot | `BEGIN; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ ONLY; ... COMMIT;` |
| A dashboard that tolerates seconds of staleness and must not block writers | `AS OF SYSTEM TIME '-10s'` |
| Read, decide, then write | Serializable read-write (default) |

Docs:

- https://camusdb.github.io/docs/distributed-transactions
- https://camusdb.github.io/docs/transactions-locking-internals
- https://camusdb.github.io/docs/time-travel-reads
- https://camusdb.github.io/docs/connection-model
