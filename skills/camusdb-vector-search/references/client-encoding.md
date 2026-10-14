# Encoding vectors in client code

Every client must write the same layout: packed float32 elements, each
little-endian, no header, no padding. The dimension is `byteLength / 4`.

## Transport encodings

| Transport | How the bytes travel |
| --- | --- |
| SQL text | Hexadecimal `X'...'` literal |
| REST (JSON) | Base64 under `bytesValue`, with `type` 7 |
| gRPC | Raw bytes in `Value.bytes_value` |

Prefer a bind parameter on every transport. The server logs statement text but
never parameter values, so embeddings stay out of log files. A 3,072-byte
vector is far below the 4 MB gRPC message default and the 30 MB request body
default.

## Worked byte example

The two-element vector `[0.0, 1.0]`:

| Element | float32 bits | Little-endian bytes |
| --- | --- | --- |
| `0.0` | `0x00000000` | `00 00 00 00` |
| `1.0` | `0x3F800000` | `00 00 80 3F` |

Packed bytes: `00 00 00 00 00 00 80 3F`.

- SQL literal: `X'000000000000803F'`
- Base64: `AAAAAAAAgD8=`

Use the literal only for a manual test:

```sql
SELECT vector_dims(X'000000000000803F');   -- 2
SELECT l2_distance(X'000000000000803F', X'000000000000803F');   -- 0
```

## REST request

```json
{
  "databaseName": "app",
  "sql": "SELECT id FROM docs ORDER BY l2_distance(embedding, @q) LIMIT 10",
  "parameters": {
    "@q": { "type": 7, "bytesValue": "AAAAAAAAgD8=" }
  }
}
```

A real 768-dimension vector is 4,096 base64 characters.

## .NET (`CamusDB.Client`)

The driver ships `CamusVector`. It writes little-endian on every host.

```csharp
using CamusDB.Client;

float[] embedding = await EmbedAsync("robot assembly manual");
byte[] packed = CamusVector.ToBytes(embedding);

await using CamusCommand insert = connection.CreateCamusCommand("""
    INSERT INTO docs (tenant_id, body, embedding)
    VALUES (@tenant, @body, @embedding)
    """);
insert.Parameters.Add("@tenant", ColumnType.Integer64, 42);
insert.Parameters.Add("@body", ColumnType.String, "robot assembly manual");
insert.Parameters.Add("@embedding", ColumnType.Bytes, packed);
await insert.ExecuteNonQueryAsync();
```

Search:

```csharp
byte[] query = CamusVector.ToBytes(await EmbedAsync("warranty claims"));

await using CamusCommand search = connection.CreateSelectCommand("""
    SELECT id, l2_distance(embedding, @query) AS distance
    FROM docs
    WHERE tenant_id = @tenant
    ORDER BY distance ASC
    LIMIT 10
    """);

search.Parameters.Add("@query", ColumnType.Bytes, query);
search.Parameters.Add("@tenant", ColumnType.Integer64, 42);
```

Read a stored vector back:

```csharp
float[] embedding = reader.GetVector(reader.GetOrdinal("embedding"));
```

Other `CamusVector` members:

| Member | Purpose |
| --- | --- |
| `ToBytes(ReadOnlySpan<float>)` | Pack into a new `byte[]`. |
| `WriteTo(ReadOnlySpan<float>, Span<byte>)` | Pack into a caller-owned buffer of at least 4 bytes per element. |
| `ToFloats(ReadOnlySpan<byte>)` | Unpack into a new `float[]`. |
| `ReadTo(ReadOnlySpan<byte>, Span<float>)` | Unpack into a caller-owned buffer. |
| `Dimensions(ReadOnlySpan<byte>)` | Byte count / 4. Throws `CamusException` `CADB0410` when not a multiple of 4. |

`ColumnType.Bytes` accepts `byte[]`, `ReadOnlyMemory<byte>`, `Memory<byte>`,
`ArraySegment<byte>` and `IEnumerable<byte>`.

Manual packing without the helper, when you cannot reference the driver:

```csharp
using System.Buffers.Binary;

static byte[] Pack(ReadOnlySpan<float> v)
{
    byte[] bytes = new byte[v.Length * 4];
    for (int i = 0; i < v.Length; i++)
        BinaryPrimitives.WriteSingleLittleEndian(bytes.AsSpan(i * 4), v[i]);
    return bytes;
}
```

Do not use `BitConverter.GetBytes(double)`. It writes 8 bytes per element.

## TypeScript (`camusdb`)

Bind with `camus.vector(...)`, or pass a `Float32Array`. Both produce `BYTES`
in the packed little-endian layout.

```ts
import { CamusClient, CamusVector, camus } from 'camusdb';

const client = new CamusClient({ endpoint: 'http://localhost:5095', database: 'app' });

await client.execute(
  'INSERT INTO docs (tenant_id, body, embedding) VALUES (@tenant, @body, @embedding)',
  {
    tenant: 42,
    body: 'robot assembly manual',
    embedding: camus.vector(embedding), // embedding: number[] or Float32Array
  },
);

const rows = await client.query<{ id: string; distance: number }>(
  `SELECT id, l2_distance(embedding, @q) AS distance
   FROM docs
   WHERE tenant_id = @tenant
   ORDER BY distance
   LIMIT 10`,
  { q: new Float32Array(queryEmbedding), tenant: 42 },
);
```

Read a stored vector back:

```ts
const row = await client.queryOne<{ embedding: Uint8Array }>('SELECT embedding FROM docs LIMIT 1');
const values = CamusVector.toFloats(row!.embedding); // Float32Array
```

`CamusVector` members: `toBytes(ArrayLike<number>)` returns `Uint8Array`,
`toFloats(Uint8Array)` returns `Float32Array`, and `dimensions(Uint8Array)`
throws with code `CADB0410` when the length is not a multiple of 4.

Parameter inference rules that matter here:

| JavaScript value | Bound as |
| --- | --- |
| `Float32Array` | `BYTES`, packed as a vector |
| `Uint8Array`, `Buffer`, `ArrayBuffer` | `BYTES`, as-is |
| `number[]` | `ARRAY`, not a vector |
| `camus.vector(ArrayLike<number>)` | `BYTES`, packed as a vector |

A plain `number[]` is the most common mistake. Wrap it in `camus.vector(...)`.

Manual packing without the helper:

```ts
function pack(values: ArrayLike<number>): Uint8Array {
  const bytes = new Uint8Array(values.length * 4);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < values.length; i++) view.setFloat32(i * 4, values[i], true);
  return bytes;
}
```

The final `true` selects little-endian. `DataView` defaults to big-endian.

## Validate before you write

CamusDB rejects `NaN` and infinity only when a vector function reads the row.
Such a row then breaks every later query that ranks it, with `CADB0412`. Check
`Number.isFinite` (TypeScript) or `float.IsFinite` (.NET) on each element
before the insert. Also reject an all-zero vector when you use
`cosine_distance`.
