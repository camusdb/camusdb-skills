# CamusDB type mapping for .NET

Read this file before you bind or read OID, UUID, NUMERIC, JSONB, FLOAT, DATE, DATETIME, BYTES, or ARRAY values.
Source docs: https://camusdb.github.io/docs/dotnet-driver#data-types and https://camusdb.github.io/docs/ef-core#type-mapping

## SQL type to driver type

| SQL DDL type | `ColumnType` (wire value) | .NET write types | .NET read |
| --- | --- | --- | --- |
| `OID`, `OBJECT_ID` | `Id` (1) | `string`, `Guid`, `CamusObjectIdValue` | `GetString` |
| `UUID`, `GUID` | `Uuid` (11) | `Guid`, canonical UUID `string` | `GetGuid` |
| `STRING`, `STRING(N)` | `String` (3) | `string` | `GetString` |
| `INT64`, `BIGINT`, `INT`, `INTEGER`, `SMALLINT` | `Integer64` (2) | `short`, `int`, `long` | `GetInt64` |
| `NUMERIC`, `DECIMAL` | `Numeric` (12) | `decimal`, decimal text `string`, integers | `GetDecimal`, `GetString` |
| `FLOAT64` | `Float64` (5) | `double`, `decimal` | `GetDouble` |
| `FLOAT32`, `REAL` | `Float32` (6) | `float` | `GetFloat` |
| `BOOL`, `BOOLEAN` | `Bool` (4) | `bool` | `GetBoolean` |
| `BYTES`, `BLOB` | `Bytes` (7) | `byte[]`, `ReadOnlyMemory<byte>`, `Memory<byte>`, `ArraySegment<byte>`, `IEnumerable<byte>` | `GetFieldValue<byte[]>` |
| `DATE` | `Date` (8) | `DateOnly`, `DateTime`, `DateTimeOffset`, ISO `string` | `GetFieldValue<DateOnly>`, `GetDateTime` |
| `DATETIME`, `TIMESTAMP` | `DateTime` (9) | `DateTime`, `DateTimeOffset`, ISO `string` | `GetDateTime`, `GetFieldValue<DateTimeOffset>` |
| `ARRAY(T)` | `Array` (10) | `IEnumerable` of a scalar type | `object?[]` |
| `JSONB`, `JSON` | `Jsonb` (13) | `JsonDocument`, `JsonElement`, `JsonNode`, `string` typed `Jsonb` | `GetString`, `GetValue`, `GetFieldValue<JsonElement>` |
| `NULL` | `Null` (0) | `null`, `DBNull.Value` | `IsDBNull` |

## Rules per type

### Integers

- All integer declarations share `ColumnType.Integer64` and the same wire values.
- The server enforces the declared column range: `SMALLINT` -32768 to 32767, `INT` and `INTEGER`
  32-bit, `INT64` and `BIGINT` 64-bit. A value outside the range fails with `CADB0420`.
- Columns created before this rule keep the full `INT64` range.

### OID

- An ObjectId is 12 bytes, written as 24 lowercase hex characters.
- Bind it with `ColumnType.Id`. A `ColumnType.String` value is a different type.
- Generate one in SQL with `GEN_ID()` or in .NET with `CamusObjectIdGenerator.Generate()` (namespace `CamusDB.Core.Util.ObjectIds`).
- `CamusObjectIdGenerator.GenerateAsString()` returns the 24-character text.
- An ObjectId starts with a timestamp. Avoid it as a key on a high-insert table under key-range sharding.

### UUID

- Use a native `UUID` column. Do not store UUID text in `STRING`.
- Bind with `ColumnType.Uuid` and a `Guid`.
- `Guid.NewGuid()` is random (version 4). It spreads inserts across ranges.
- SQL generators: `gen_uuid_v4()` (random) and `gen_uuid_v7()` (time-ordered).

### NUMERIC

- `NUMERIC` is exact, with precision 38 and scale 9.
- A `decimal` parameter binds as `NUMERIC`.
- For more digits than `decimal` holds, bind decimal text: `Parameters.Add("@v", ColumnType.Numeric, "12345678901234567890.123456789")`.
- Read small values with `GetDecimal()`. Read every value without rounding with `GetString()`.
- The server rounds past nine fraction digits. A value out of range fails with `CADB0417`.
- EF Core `HasPrecision(...)` does not change the column type.

### JSONB

- A `JsonDocument`, `JsonElement`, or `JsonNode` parameter binds as `JSONB` with no `ColumnType` set.
- A `string` typed `ColumnType.Jsonb` binds its text. A plain `string` binds as `STRING`, which a `jsonb` column parses on write.
- `GetString` and `GetValue` return the PostgreSQL text form. `GetFieldType` is `string`.
- `GetFieldValue<T>` parses into `JsonDocument`, `JsonElement`, `JsonNode`, `JsonObject`, or `JsonArray`. The wrong kind throws `InvalidCastException`.
- `CamusJsonb.Format(json)` gives the PostgreSQL text form of any JSON text.
- Invalid JSON fails with `CADB0418`. Nesting deeper than 512 levels fails with `CADB0419`.
- A list of documents fails with `CADB0400`. Bind one JSON array instead.
- JSON `null` reads as the text `null`. SQL `NULL` reads as `DBNull`.
- Over REST, the driver sends `jsonbAsText` to keep those two apart. An older server ignores it and gives a top-level JSON `null` as SQL `NULL`.
- A client before 0.17.0 reads every `jsonb` cell over gRPC as `NULL`, with no error.

### Infinity and NaN

- `Infinity`, `-Infinity`, and `NaN` are `FLOAT64` and `FLOAT32` values on both transports.
- Since 0.17.2, the driver reads them over REST as float values, and sends non-finite parameters.
- Any other string in a float cell stays a `String`.
- A server older than the PostgreSQL float rule cannot return these values over REST, and refuses them as parameters.

### DATE and DATETIME

- The driver converts to UTC before it sends a value.
- `DATE` is stored at midnight UTC.
- `DATETIME` reads back with `DateTimeKind.Utc`.
- On the wire both are .NET ticks in UTC.

### BYTES and vectors

- SQL literals use `X'...'`. JSON uses base64.
- A vector is packed little-endian `float32` in `BYTES`. Use `CamusVector.ToBytes(...)` and `GetVector(...)`.

### ARRAY

- Bind with `Parameters.Add(name, elementType, values, isArray: true)`.
- Always pass the element type for an empty array or an all-`NULL` array.
- `ARRAY(NUMERIC)` is not supported. An array of `decimal` binds as `ARRAY(FLOAT64)`.
- `ARRAY(JSONB)` is not supported.
- Arrays are whole values. They are not indexable.
- Test membership with `= ANY(@values)` or `= ANY(column)`.

## EF Core CLR mapping

| CLR type | Store type for `HasColumnType` | DDL |
| --- | --- | --- |
| `string` key with `HasColumnType("id")` | `id` or `oid` | `OID` |
| `Guid` | `uuid` or `guid` (optional) | `UUID` |
| `string` | `string` | `STRING` |
| `string` with `HasMaxLength(n)` | `string` | `STRING(n)` |
| `bool` | `bool` | `BOOL` |
| `short`, `int`, `long` | `int64` | `INT64` |
| `float` | `float32` or `real` | `FLOAT32` |
| `double` | `float64` | `FLOAT64` |
| `decimal` | `numeric` or `decimal` | `NUMERIC` |
| `byte[]` | `bytes` or `blob` | `BYTES` |
| `DateOnly` | `date` | `DATE` |
| `DateTime`, `DateTimeOffset` | `datetime` or `timestamp` | `DATETIME` |
| `long[]`, `string[]`, `double[]`, `bool[]` | `array(int64)`, `array(string)`, `array(float64)`, `array(bool)` | `ARRAY(T)` |
| `JsonDocument`, `JsonElement`, `JsonNode`, `JsonObject`, `JsonArray` | `jsonb` or `json` | `JSONB` |
| `string` with `HasColumnType("jsonb")` | `jsonb` or `json` | `JSONB` |
| `float[]` or `byte[]` with `IsVector(n)` | set by `IsVector` | `BYTES(n x 4)` |

- Key CLR types must be `string`, `Guid`, `short`, `int`, or `long`.
- Never map a `Guid` with `HasColumnType("id")`.
- A `NUMERIC` value wider than `decimal` throws on materialization. Use a `string` property for it.

## Dapper mapping

- Common scalars bind by .NET type.
- `decimal` binds as `NUMERIC`. A `string` property reads `NUMERIC` with every digit.
- Use `CamusValue.Id`, `CamusValue.Uuid`, `CamusValue.Numeric`, `CamusValue.Jsonb`, `CamusValue.Array`, `CamusValue.Vector` for explicit types.
- `CamusObjectIdValue` binds and reads directly after `CamusDapper.Register()`. So do the `System.Text.Json` document types.

## HTTP and gRPC wire values

- REST `ColumnValue.type` uses the integer values in the first table.
- REST writes a non-finite float as the string `"Infinity"`, `"-Infinity"`, or `"NaN"`.
- gRPC uses the `Value` oneof: `id_value`, `int64_value`, `string_value`, `bool_value`, `float64_value`, `float32_value`, `bytes_value`, `date_value`, `datetime_value`, `array_value`, `uuid_value`, `numeric_value`, `json_value`, `null_value`.
- gRPC sends a UUID as 16 big-endian bytes and dates as UTC ticks: `ticks = 621355968000000000 + unix_millis * 10000`.
