# CamusDB query result cache

The result cache stores a fully materialized `SELECT` result in memory on one
node. A later identical query skips storage and returns the stored rows. It is
enabled by default and does nothing until a query opts in with a hint.

## When to use it

Add a hint only when all of these hold:

- The query reads one table. No join.
- It runs in autocommit mode, not inside `BEGIN ... COMMIT`.
- It repeats often with the same parameter values.
- Its data changes much less often than it is read.
- Its result is small: at most 10,000 rows and 1 MiB by default.
- Its result is deterministic. Do not hint a query that uses `NOW()`, random
  values or other volatile inputs.

## Syntax

Put the hint right after the table reference and its optional alias:

```sql
SELECT id, total FROM orders {cache=recent_orders} WHERE status = 1 ORDER BY total DESC LIMIT 20;
SELECT * FROM orders {cache=hot_orders, ttl=30s};
SELECT * FROM orders {cache=hot_orders, strict};
SELECT * FROM orders {cache=hot_orders, ttl=5m, strict};
SELECT * FROM orders o @{cache=hot_orders, ttl=30s};   -- @ form is an alias
```

- `cache=<name>` names a family. Names are lowercased, so they are
  case-insensitive.
- `ttl=<n>[ms|s|m|h]`. A bare integer is milliseconds. `ttl=0`, overflow, an
  unknown unit or a non-integer are parse errors.
- `strict` validates every hit against live storage.
- One hint per statement. Unknown options are parse errors.
- The hint applies to the whole `SELECT` result.
- A hint on a join, or inside a `WHERE` subquery, is ignored.

## Eviction

```sql
EVICT CACHE 'recent_orders';   -- one family, current database
EVICT CACHE ALL;               -- every entry of the current database
```

`EVICT` is reserved. `CACHE` and `ALL` are not.

## Freshness guarantees

| Writer location | Guarantee |
| --- | --- |
| Same node | Exact. A commit evicts every dependent entry before a later probe can see it. |
| Another node | Stale up to the entry TTL (default 5,000 ms). |
| Another node, `strict` entry | Each hit is validated against storage. Stale entries are re-executed. |

DDL on a table evicts entries that depend on it. The fingerprint includes
schema versions, so post-DDL queries never match old entries.

`strict` cost: up to a range scan per hit, bounded by
`query_result_cache_strict_validation_max_keys` (10,000). Past the bound the
entry counts as stale. Use `strict` for correctness across nodes, not for speed.

## Cache key

A 128-bit XxHash128 fingerprint over: database id, family name, query shape id,
every bound parameter with its type, schema versions, and cache options. Two
queries share an entry only when all parts match. Different parameter values
are different entries.

## Observe the outcome

Every hinted response carries cache metadata. REST returns fields. gRPC sends a
trailing `CacheMetadata` message.

| Field | Meaning |
| --- | --- |
| `cacheStatus` | `hit`, `miss`, `bypass`, `stale-revalidated`, `evicted-before-publish` |
| `cacheBypassReason` | Why a bypass or failed publish happened |
| `cacheName` | Family name, when eligible |
| `cachedAtHlc` | HLC of the served entry, hits only |
| `ageMs` | Approximate age of the served entry, hits only |

Status meanings:

- `hit`: stored rows served.
- `miss`: executed live, entry stored.
- `bypass`: executed live, nothing stored.
- `evicted-before-publish`: executed live with correct rows, entry not stored.
- `stale-revalidated`: strict entry was stale, so the query ran live again.

Bypass reasons:

| Reason | Meaning | Action |
| --- | --- | --- |
| `in-flight-write` | A write was committing into the scanned key space | Expected under writes; nothing to fix |
| `cache-disabled` | `query_result_cache_enabled: false` | Enable it or remove the hint |
| `oversized-result` | Result exceeded row or byte caps, or the cache is full | Add `LIMIT`, narrow columns, or raise caps |
| `dependency-limit` | Too many dependencies, or a strict entry lost point dependencies to the cap | Narrow the query |

`EXPLAIN` adds a static `cache` row:
`family=<name>, eligible=<true|false>[, reason=<join|cache-disabled>], ttl=<n>ms|default, strict=<bool>`.
It does not probe the cache.

## Settings (config.yml only)

| Key | Default | Meaning |
| --- | --- | --- |
| `query_result_cache_enabled` | `true` | Main switch |
| `query_result_cache_default_ttl_ms` | `5000` | TTL when the hint has no `ttl` |
| `query_result_cache_max_entries` | `1024` | Entry cap, LRU eviction |
| `query_result_cache_max_bytes` | `67108864` | Total byte budget (64 MiB) |
| `query_result_cache_max_entry_bytes` | `1048576` | Per-entry byte cap (1 MiB) |
| `query_result_cache_max_entry_rows` | `10000` | Per-entry row cap |
| `query_result_cache_max_deps` | `4096` | Dependency cap; above it, no publish |
| `query_result_cache_max_point_deps` | `2048` | Point dependency cap; strict entries bypass above it |
| `query_result_cache_max_ranges` | `256` | Range dependency cap |
| `query_result_cache_singleflight_wait_ms` | `250` | Wait for a concurrent identical miss |
| `query_result_cache_strict_validation_max_keys` | `10000` | Strict validation key budget |
| `query_result_cache_sweep_interval_ms` | `10000` | Background TTL sweep interval |

Every cap fails safe: the query returns correct live rows and the entry is not
stored.

## Interactions

- Writes (`UPDATE ... WHERE`, `DELETE ... WHERE`) never read the cache.
- A cache-eligible query stays local; distributed execution does not split it.
- A cache-hinted statement gets no SQL routing advice.
- The cache is per node, in memory, and lost on restart. A hit on node A says
  nothing about node B.
- The result cache is separate from the plan cache. The plan cache reuses
  optimizer decisions; the result cache returns stored rows.

## Common mistakes

- Hinting a join. It always bypasses (`reason=join`).
- Hinting inside an explicit transaction, including `READ ONLY`. It reads live
  storage.
- Expecting another node's write to evict this node's entry. Use a short `ttl`
  or `strict`.
- Expecting a different parameter value to hit the same entry.
- Hinting a large result. It exceeds 10,000 rows or 1 MiB and is never stored.

Source: https://camusdb.github.io/docs/query-result-cache
