# CamusDB type mapping for .NET

Read this file before you bind or read OID, UUID, NUMERIC, DATE, DATETIME, BYTES, or ARRAY values.
Source docs: https://camusdb.github.io/docs/dotnet-driver#data-types and https://camusdb.github.io/docs/ef-core#type-mapping

## SQL type to driver type

| SQL DDL type | `ColumnType` (wire value) | .NET write types | .NET read |
| --- | --- | --- | --- |
| `OID`, `OBJECT_ID` | `Id` (1) | `string`, `Guid`, `CamusObjectIdValue` | `GetString` |
| `UUID`, `GUID` | `Uuid` (11) | `Guid`, canonical UUID `string` | `GetGuid` |
| `STRING`, `STRING(N)` | `String` (3) | `string` | `GetString` |
| `INT64`, `INT`, `INTEGER` | `Integer64` (2) | `short`, `int`, `long` | `GetInt64` |
| `NUMERIC`, `DECIMAL` | `Numeric` (12) | `decimal`, decimal text `string`, integers | `GetDecimal`, `GetString` |
| `FLOAT64` | `Float64` (5) | `double`, `decimal` | `GetDouble` |
| `FLOAT32`, `REAL` | `Float32` (6) | `float` | `GetFloat` |
| `BOOL`, `BOOLEAN` | `Bool` (4) | `bool` | `GetBoolean` |
| `BYTES`, `BLOB` | `Bytes` (7) | `byte[]`, `ReadOnlyMemory<byte>`, `Memory<byte>`, `ArraySegment<byte>`, `IEnumerable<byte>` | `GetFieldValue<byte[]>` |
| `DATE` | `Date` (8) | `DateOnly`, `DateTime`, `DateTimeOffset`, ISO `string` | `GetFieldValue<DateOnly>`, `GetDateTime` |
| `DATETIME`, `TIMESTAMP` | `DateTime` (9) | `DateTime`, `DateTimeOffset`, ISO `string` | `GetDateTime`, `GetFieldValue<DateTimeOffset>` |
| `ARRAY(T)` | `Array` (10) | `IEnumerable` of a scalar type | `object?[]` |
| `NULL` | `Null` (0) | `null`, `DBNull.Value` | `IsDBNull` |

## Rules per type

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
- `ARRAY(NUMERIC)` is not supported.
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

- Key CLR types must be `string`, `Guid`, `short`, `int`, or `long`.
- Never map a `Guid` with `HasColumnType("id")`.
- A `NUMERIC` value wider than `decimal` throws on materialization. Use a `string` property for it.

## Dapper mapping

- Common scalars bind by .NET type.
- `decimal` binds as `NUMERIC`. A `string` property reads `NUMERIC` with every digit.
- Use `CamusValue.Id`, `CamusValue.Uuid`, `CamusValue.Numeric`, `CamusValue.Array`, `CamusValue.Vector` for explicit types.
- `CamusObjectIdValue` binds and reads directly after `CamusDapper.Register()`.

## HTTP and gRPC wire values

- REST `ColumnValue.type` uses the integer values in the first table.
- gRPC uses the `Value` oneof: `id_value`, `int64_value`, `string_value`, `bool_value`, `float64_value`, `float32_value`, `bytes_value`, `date_value`, `datetime_value`, `array_value`, `uuid_value`, `null_value`.
- gRPC sends a UUID as 16 big-endian bytes and dates as UTC ticks: `ticks = 621355968000000000 + unix_millis * 10000`.
