---
name: camusdb-vector-search
description: Builds embedding and nearest-neighbour (KNN) search on CamusDB. Covers storing float32 vectors in BYTES columns with STORAGE PLAIN and CHECK (vector_dims(...) = N), packing vectors in .NET (CamusVector.ToBytes, ColumnType.Bytes), EF Core (IsVector, float[] properties, loadWithEntity, EF.Functions.L2Distance, CosineDistance, InnerProduct, VectorDims), Dapper (CamusValue.Vector) and TypeScript (camus.vector, Float32Array, CamusVector.toFloats), and ranking with l2_distance, cosine_distance and inner_product in ORDER BY ... LIMIT k queries, including nearest rows per group with LATERAL. Use when a user mentions embeddings, semantic search, RAG retrieval, similarity search, pgvector or HNSW habits, vector_dims, octet_length, topk in EXPLAIN, or errors CADB0410, CADB0411, CADB0412 or CADB0302 on a vector column.
---

# CamusDB vector search

CamusDB has no vector type and no vector index. A vector is a `BYTES` value of
packed little-endian float32 numbers. A nearest-neighbour query is an ordinary
`SELECT` with a distance function in `ORDER BY` and a `LIMIT`. The search is
exact: CamusDB reads and ranks every row that the `WHERE` clause admits.

Use this skill to design the table, encode vectors in the client, write the
query, and keep it fast.

## Workflow

Follow these steps in order.

1. Get the embedding dimension from the model (for example 768 or 1536).
2. Compute the byte size: dimension × 4. A 768-dimension vector is 3,072 bytes.
3. Create the column as `BYTES(<bytes>) NOT NULL STORAGE PLAIN`.
4. Add `CHECK (vector_dims(<col>) = <dimension>)`.
5. Pick one metric. Use the same metric the model was trained for.
6. Encode vectors in the client as packed little-endian float32. Read
   [references/client-encoding.md](references/client-encoding.md) for .NET,
   EF Core, Dapper, TypeScript, REST, gRPC and SQL hex.
7. Send the query vector as a bind parameter, never as a literal. Pack it with the driver helper:
   `CamusVector.ToBytes` (.NET), `CamusValue.Vector` (Dapper), `camus.vector` (TypeScript).
   Python has no helper; bind packed float32 `bytes`.
8. Write `ORDER BY <metric>(col, @q) [DESC] LIMIT k`. Add a selective `WHERE`.
9. Run `EXPLAIN` and confirm a `topk` node, not a `sort` node.
10. Estimate the cost from row count × dimension. Read
    [references/storage-and-cost.md](references/storage-and-cost.md) before you
    scan more than a few hundred thousand rows.

## The storage contract

A vector value has this layout:

1. Each element is an IEEE-754 float32.
2. Each element is little-endian.
3. Elements are packed. There is no header and no padding.

The dimension is the byte count divided by 4. The schema does not record the
layout. Every client must follow it. The float32 `1.0` is the bytes
`00 00 80 3F`.

## Create the table

```sql
CREATE TABLE docs (
  id OID PRIMARY KEY NOT NULL DEFAULT (gen_id()),
  tenant_id INT64 NOT NULL,
  body STRING NOT NULL,
  embedding BYTES(3072) NOT NULL STORAGE PLAIN,
  CONSTRAINT embedding_is_768d CHECK (vector_dims(embedding) = 768)
);

CREATE INDEX docs_tenant_idx ON docs (tenant_id);
```

Each part has a job:

- `BYTES(3072)` is a maximum, not a fixed width. A longer value fails with
  `CADB0302 ValueTooLong`. A shorter value is accepted.
- `CHECK (vector_dims(embedding) = 768)` fixes the dimension. A wrong
  dimension fails the write with `CADB0303 CheckConstraintViolation`.
- `vector_dims` rejects a byte count that is not a multiple of 4. It does not
  round down.
- `NOT NULL` forbids a missing vector. The `CHECK` cannot do it, because
  `vector_dims(NULL)` is `NULL` and a `NULL` check passes.
- `STORAGE PLAIN` keeps the vector inside the row. A 3,072-byte value is above
  the default 2,048-byte out-of-line threshold. Under the default `EXTENDED`
  strategy, every KNN scan pays an extra fetch per batch.

Add the check to an existing table. CamusDB scans existing rows first:

```sql
ALTER TABLE docs ADD CONSTRAINT embedding_is_768d CHECK (vector_dims(embedding) = 768);
```

Fix the storage of an existing table, then rewrite the stored rows:

```sql
ALTER TABLE docs ALTER COLUMN embedding SET STORAGE PLAIN;
ALTER TABLE docs REWRITE STORAGE;
```

`SET STORAGE` changes future writes only. `REWRITE STORAGE` converts existing
rows in batches. It is resumable and idempotent.

## EF Core

`IsVector(dimensions)` sets the column type, `STORAGE PLAIN` and the dimension
`CHECK` in one call. It needs `CamusDB.EntityFrameworkCore` 0.16.10 or later. The
property can be `float[]` or `byte[]`:

```csharp
modelBuilder.Entity<DocumentChunk>(b =>
{
    b.Property(c => c.Embedding).IsRequired().IsVector(768, loadWithEntity: false);
});

var hits = await ctx.Chunks
    .Where(c => c.TenantId == tenantId)
    .OrderBy(c => EF.Functions.L2Distance(c.Embedding, question))
    .Take(10)
    .ToListAsync();
```

Read [references/client-encoding.md](references/client-encoding.md) for the helpers
and for `loadWithEntity: false`.

## Distance functions

All three take two `BYTES` vectors of equal dimension and return `FLOAT64`.

| Function | Meaning | Nearest is | Sort |
| --- | --- | --- | --- |
| `l2_distance(a, b)` | Euclidean distance, with the square root | smaller | `ASC` |
| `cosine_distance(a, b)` | `1 - cosine_similarity`, range 0 to 2 | smaller | `ASC` |
| `inner_product(a, b)` | Dot product | larger | `DESC` |

`inner_product` sorts in the opposite direction. An `ASC` sort returns the
least similar rows. CamusDB raises no error for this mistake.

A `NULL` argument returns `NULL`. CamusDB widens every element to `double`
before arithmetic. SIMD kernels run where the hardware supports them.

## Query patterns

Basic KNN. Always add `LIMIT`:

```sql
SELECT id
FROM docs
WHERE tenant_id = @tenant
ORDER BY l2_distance(embedding, @q)
LIMIT 10;
```

Return the distance. Sort by the alias:

```sql
SELECT id, body, cosine_distance(embedding, @q) AS distance
FROM docs
WHERE tenant_id = @tenant
ORDER BY distance
LIMIT 10;
```

Inner product. Sort `DESC`:

```sql
SELECT id FROM docs ORDER BY inner_product(embedding, @q) DESC LIMIT 10;
```

Stable order among equal distances. Add a tie-breaker:

```sql
SELECT id, l2_distance(embedding, @q) AS distance
FROM docs
ORDER BY distance, id
LIMIT 10;
```

Nearest rows per group. Use a `LATERAL` source with its own `ORDER BY ... LIMIT`:

```sql
SELECT t.id AS tenant_id, hit.id, hit.distance
FROM tenants AS t
CROSS JOIN LATERAL (
  SELECT id, l2_distance(embedding, @q) AS distance
  FROM docs
  WHERE tenant_id = t.id
  ORDER BY distance
  LIMIT 3
) AS hit;
```

Rules for these queries:

- A `LATERAL` body runs once per left row, as a nested loop in the written join
  order. Filter the left side first. `EXPLAIN` marks the join `lateral`.
- An alias in `ORDER BY` outranks a base column of the same name.
- `ORDER BY` can use a column that the `SELECT` list does not project.
- `LIMIT` accepts a parameter, such as `LIMIT @k`. The bound still applies.
- `OFFSET` pages cost more. CamusDB keeps `offset + limit` rows in the `topk`.
- A computed `ORDER BY` expression with `GROUP BY` fails with
  `CADB0400 InvalidInput`. Project the value first, then sort by the alias.
- Distance thresholds work as filters, for example
  `WHERE l2_distance(embedding, @q) < 0.5`. The filter still computes the
  distance for every candidate row.

## Confirm the plan

```sql
EXPLAIN SELECT id FROM docs ORDER BY l2_distance(embedding, @q) LIMIT 10;
```

Look for a `topk` row with detail `k: 10, l2_distance(…) ASC`. A `topk` holds at
most `offset + limit` rows. With spill on, it moves to disk only when those rows
pass `spill_threshold_bytes`.

A `sort` row means the bound did not apply. CamusDB keeps the full sort when:

- `LIMIT` is missing, unknown, or negative.
- `OFFSET` is negative.
- `offset + limit` overflows or exceeds the maximum 32-bit integer.
- Spill is enabled and `offset + limit` exceeds `spill_threshold_rows`.
- An aggregate sits between the sort and the limit.

`EXPLAIN (ANALYZE)` runs the query. The scan row reports every examined row in
`rows_read`. The `topk` row reports only the rows it kept.

## Performance rules

1. Always pair a vector `ORDER BY` with a `LIMIT`. Measured: 12 ms with
   `LIMIT 10` against 50 ms without, on 10,000 rows at 768 dimensions.
2. Narrow candidates with an indexed `WHERE`, such as `tenant_id = @tenant`.
   The filter runs before the rank.
3. Use `STORAGE PLAIN` on the embedding column.
4. Do not project the embedding. Return ids and small columns. A buffered response
   over `max_buffered_result_bytes` (256 MiB) fails with `CADB0556`.
5. Expect linear cost in rows × dimensions. About 1.2 s per million rows at
   768 dimensions on an Apple M3.
6. The value of `k` barely matters. `LIMIT 10` and `LIMIT 100` cost the same.
7. Send the vector as a parameter. The statement text then stays constant, and a
   repeat skips the parse. The plan cache is off by default, so expect no shared plan.
   The .NET and TypeScript drivers prepare repeated statements automatically; keep
   supplying named parameters.

## Errors

| Condition | Error |
| --- | --- |
| Byte count not divisible by 4 | `CADB0410` malformed vector |
| Vector with zero elements | `CADB0410` malformed vector |
| Operands of different dimensions | `CADB0411` dimension mismatch |
| An element is `NaN` or infinity | `CADB0412` invalid vector value |
| `cosine_distance` gets a zero-magnitude vector | `CADB0412` invalid vector value |
| Value longer than `BYTES(N)` | `CADB0302` ValueTooLong |
| Wrong dimension under the `CHECK` | `CADB0303` CheckConstraintViolation |
| A non-`BYTES` argument to a vector function | `CADB0400` InvalidInput |
| Buffered response over `max_buffered_result_rows` or `max_buffered_result_bytes` | `CADB0556` ResultSetTooLarge |

CamusDB checks `NaN` and infinity when a vector function reads the value, not at
write time. Validate embeddings in the client before the insert.

## Transactions, time travel and clusters

- A KNN query obeys normal visibility. It ranks the uncommitted rows of its own
  transaction.
- It works with `AS OF SYSTEM TIME`, in autocommit mode:

```sql
SELECT id
FROM docs AS OF SYSTEM TIME '-10s'
ORDER BY l2_distance(embedding, @q)
LIMIT 10;
```

- In a cluster, the coordinator ranks the whole table. There is no distributed
  top-k. Every qualifying row can cross the network to the coordinator.

## Limits

- No approximate index: no HNSW, no IVF, no quantization.
- No vector index type. An ordinary index cannot serve a distance sort.
- A GIN index does not help. It needs a `jsonb` column.
- No native vector type. The dimension lives only in the `CHECK`.
- No distributed top-k.

## Common mistakes

- Declaring `embedding vector(768)` as in pgvector. Use
  `BYTES(3072)` plus a `CHECK` on `vector_dims`.
- Using pgvector operators `<->`, `<=>` or `<#>`. Call `l2_distance`,
  `cosine_distance` or `inner_product`.
- Creating an HNSW or IVFFlat index, or setting a probes or `ef_search`
  parameter. None exists. Narrow with `WHERE` instead.
- Creating a GIN index on the embedding. CamusDB refuses it on a non-`jsonb` column.
- Declaring an EF Core embedding without `IsVector`. You then lose `STORAGE PLAIN`,
  the dimension `CHECK` and the `float[]` conversion.
- Ordering `EF.Functions.InnerProduct` with `OrderBy`. Use `OrderByDescending`.
- Sorting `inner_product` `ASC`. pgvector's `<#>` returns the negative inner
  product. CamusDB returns the plain dot product. Sort `DESC`.
- Storing the vector as `ARRAY(FLOAT32)` as in Spanner. Distance functions
  accept `BYTES` only.
- Packing float64 (8 bytes per element) or big-endian data. Use float32,
  little-endian.
- Passing a plain JavaScript `number[]`. The TypeScript connector binds it as an
  `ARRAY`. Use `camus.vector(values)` or a `Float32Array`.
- Trusting `BYTES(N)` to enforce an exact length. It is a maximum only.
- Relying on the `CHECK` to reject `NULL`. Add `NOT NULL`.
- Leaving the default `EXTENDED` storage. A 768-dimension vector moves out of
  the row, and every scan fetches it separately.
- Writing the query vector as an `X'...'` literal. It adds about 6 KB of text
  per statement and changes the statement text each time.
- Omitting `LIMIT`. The plan then shows `sort` and materializes every row.
- Expecting `l2_distance` to return the squared distance. It applies the
  square root.

## References

- [references/client-encoding.md](references/client-encoding.md): read when
  writing client code in .NET, EF Core, Dapper, TypeScript, REST, gRPC, or SQL text.
- [references/storage-and-cost.md](references/storage-and-cost.md): read when
  sizing a table, choosing a storage strategy, or predicting latency.
- Public docs:
  - https://camusdb.github.io/docs/vector-search
  - https://camusdb.github.io/docs/data-types
  - https://camusdb.github.io/docs/functions-bytes
  - https://camusdb.github.io/docs/storage-layout
  - https://camusdb.github.io/docs/check-constraints
  - https://camusdb.github.io/docs/dotnet-driver
  - https://camusdb.github.io/docs/ef-core
  - https://camusdb.github.io/docs/dapper
  - https://camusdb.github.io/docs/typescript-connector
  - https://camusdb.github.io/docs/http-api (REST `bytesValue` encoding)
  - https://camusdb.github.io/docs/grpc-api (gRPC `bytes_value` encoding)
  - https://camusdb.github.io/docs/joins-and-subqueries (`LATERAL` sources)
  - https://camusdb.github.io/docs/explain
