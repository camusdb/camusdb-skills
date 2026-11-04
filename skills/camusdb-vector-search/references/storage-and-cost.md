# Vector storage strategy and cost

Read this file when you size a table, pick a storage strategy, or predict the
latency of a KNN query.

## Size per dimension

| Dimensions | Bytes per vector | Column type |
| ---: | ---: | --- |
| 128 | 512 | `BYTES(512)` |
| 384 | 1,536 | `BYTES(1536)` |
| 768 | 3,072 | `BYTES(3072)` |
| 1,024 | 4,096 | `BYTES(4096)` |
| 1,536 | 6,144 | `BYTES(6144)` |
| 3,072 | 12,288 | `BYTES(12288)` |

Bytes = dimensions × 4. A bare `BYTES` column allows up to 10,485,760 bytes.

## Storage strategies

Each `STRING`, `BYTES`, `JSONB` and `ARRAY` column has one strategy.

| Strategy | Compresses | Out of line | Use for vectors when |
| --- | --- | --- | --- |
| `PLAIN` | Never | Never | KNN queries scan the column. This is the normal choice. |
| `EXTERNAL` | Never | At or above the threshold | Most queries skip the vector and only background jobs read it. |
| `EXTENDED` | When it saves enough | At or above the threshold | Default. Wrong for KNN: float32 data rarely compresses, and large vectors move out of the row. |
| `MAIN` | When it saves enough | Never | Not useful for vectors. |

Defaults that decide the behavior:

| Setting | Default |
| --- | --- |
| `large_value_threshold_bytes` | `2048` |
| `large_value_compression_enabled` | `true` |
| `large_value_compression_min_saving_percent` | `12` |
| `large_value_rewrite_batch_rows` | `200` |

Compression is tried only for values of at least 256 bytes. It is kept only
when it saves at least 12 percent. Float32 data rarely does. Under `EXTENDED`, a
vector of 512 dimensions or more (2,048 bytes) therefore usually moves out of
the row.

A strategy never changes query results. It changes the I/O of each scan.

Set the strategy at creation:

```sql
CREATE TABLE docs (
  id OID PRIMARY KEY NOT NULL DEFAULT (gen_id()),
  title STRING,
  embedding BYTES(3072) NOT NULL STORAGE PLAIN,
  CONSTRAINT embedding_is_768d CHECK (vector_dims(embedding) = 768)
);
```

Change it later. `SET STORAGE` affects future writes. `REWRITE STORAGE`
converts existing rows:

```sql
ALTER TABLE docs ALTER COLUMN embedding SET STORAGE PLAIN;
ALTER TABLE docs REWRITE STORAGE;
```

`REWRITE STORAGE` runs in transactions of `large_value_rewrite_batch_rows`. It
is idempotent and resumable. It can make concurrent writes retry with
`CADB0502 TransactionConflict`. Run it in a quiet period.

EF Core `IsVector` sets `STORAGE PLAIN` for you. A later `HasStorage` replaces it.

`SHOW CREATE TABLE` shows a strategy that was set explicitly. A strategy on a
non-variable-length column fails with `CADB0414 ColumnStorageNotApplicable`.

## Write cost

Each out-of-line value is one extra mutation. With `PLAIN`, the vector adds no
extra mutation. The default limit is 20,000 mutations per transaction. Batch
large imports to stay below it.

An update that does not assign the embedding does not rewrite an out-of-line
value. With `PLAIN`, an update of any column rewrites the whole row, vector
included. Keep often-updated counters in another table when rows carry large
vectors.

## Read cost

Measured on an Apple M3, 10,000 rows, 768 dimensions, SIMD active:

| Query | Time |
| --- | ---: |
| `ORDER BY l2_distance(...) LIMIT 10` | 12 ms |
| `ORDER BY l2_distance(...)` without a limit | 50 ms |
| `SELECT id FROM docs` without a sort | 6 ms |

One distance evaluation on the same hardware:

| Dimensions | `l2_distance` | `inner_product` | `cosine_distance` |
| ---: | ---: | ---: | ---: |
| 128 | 53 ns | 50 ns | 67 ns |
| 768 | 240 ns | 214 ns | 320 ns |
| 1536 | 459 ns | 405 ns | 607 ns |

Cost grows linearly with rows × dimensions. The docs estimate about 1.2 s for
one million rows at 768 dimensions on the same hardware.

Quick estimate: candidate rows × per-evaluation time, plus the scan itself.
Example: 200,000 candidate rows × 240 ns ≈ 48 ms of distance work.

## Ways to cut cost

1. Filter first. An indexed predicate such as `tenant_id = @tenant` reduces the
   candidate rows. Check it in `EXPLAIN`: the scan should be `index-range-scan`
   or `index-lookup`, not `table-scan`.
2. Always use `LIMIT`. It turns `sort` into `topk`.
3. Use a smaller model dimension when quality allows. Cost is linear in it.
4. Keep `STORAGE PLAIN`.
5. Avoid `OFFSET` for deep pages. The `topk` keeps `offset + limit` rows.
6. In a cluster, run the query on a node near the data. The coordinator ranks
   every qualifying row, and there is no distributed top-k.
7. In EF Core, use `IsVector(n, loadWithEntity: false)` when list pages do not read the
   vector.

## What does not help

- A secondary index on the embedding column. Distance is not an
  order-preserving function of the bytes.
- A GIN index. It needs a `jsonb` column.
- Changing `k`. `LIMIT 10` and `LIMIT 100` measure the same.
- `{cache=...}` result-cache hints, unless the same query vector repeats. The
  cache key includes every bound value.
