# CamusDB gRPC API overview

Read this file before you build a gRPC client from the proto, or debug the connector's gRPC transport.
The `camusdb` connector already implements this protocol. Set `protocol: 'grpc'` instead of writing your own when you use Node.js.
Source docs: https://camusdb.github.io/docs/grpc-api

## Basics

- The proto contract is `CamusDB.Grpc.Contracts/Protos/camus_sql.proto` in the CamusDB source tree. The connector ships a copy in `proto/camus_sql.proto`.
- The proto declares no package. Services are `CamusSql`, `CamusRows`, and `CamusAuth`.
- The gRPC listener uses HTTP/2 on its own port (`grpc_port`, `5096` in the docs).
- Without a certificate, the listener uses plaintext HTTP/2. Use TLS in production.
- Send `authorization: Bearer camus_<id>.<secret>` metadata when authentication is on.

## Services

| Service | RPC | Shape | Purpose |
| --- | --- | --- | --- |
| `CamusAuth` | `Login` | unary | `LoginRequest { user, password }` returns `LoginReply { token, expires_at_unix_ms, expires_in_seconds }` |
| `CamusAuth` | `Logout` | unary | Revokes the token in metadata |
| `CamusSql` | `ExecuteQuery` | server stream | `SELECT`, `SHOW`; schema first, then rows |
| `CamusSql` | `ExecuteNonQuery` | unary | `INSERT`, `UPDATE`, `DELETE`, account statements |
| `CamusSql` | `ExecuteDdl` | unary | Schema statements |
| `CamusSql` | `StartTransaction` | unary | Returns a `TxnHandle` |
| `CamusSql` | `CommitTransaction`, `RollbackTransaction` | unary | Finalize by handle |
| `CamusSql` | `BatchExecute` | bidirectional stream | Pipelined SQL, transactions, prepared statements |
| `CamusSql` | `Ping` | unary | Liveness |
| `CamusRows` | `InsertRow`, `Query`, `QueryById`, `UpdateRows`, `UpdateById`, `DeleteRows`, `DeleteById` | mixed | Typed row CRUD without SQL text |

## `SqlRequest`

Fields: `database`, `sql`, `parameters` (`map<string, Value>`), `txn_handle`, `isolation_level`, `transaction_mode`, `locking`, `causal_token_l`, `causal_token_c`, `causal_token_n`, `statement_id`, `positional_parameters`, `priority`, `routing_accept_version`.

- Omit `txn_handle` for autocommit.
- Enum values: `READ_COMMITTED` / `SERIALIZABLE`, `READ_WRITE` / `READ_ONLY`, `PESSIMISTIC` / `OPTIMISTIC`, priority `BACKGROUND` to `CRITICAL`.
- Carry all three causal-token parts (`n`, `l`, `c`) into the next request. Do not drop `n`.
- Keep the latest causal token in the `TxnHandle` for resume, commit, and rollback.

## `Value` encoding

| Column type | Field | Encoding |
| --- | --- | --- |
| NULL | `null_value` | typed NULL sentinel |
| ID / OID | `id_value` | 24 lowercase hex characters |
| INT64 | `int64_value` | signed 64-bit |
| STRING | `string_value` | UTF-8 |
| BOOL | `bool_value` | boolean |
| FLOAT64 | `float64_value` | double |
| FLOAT32 | `float32_value` | float |
| BYTES | `bytes_value` | raw bytes |
| DATE | `date_value` | UTC .NET ticks at midnight |
| DATETIME | `datetime_value` | UTC .NET ticks |
| ARRAY | `array_value` | element type plus nested `Value` items |
| UUID / GUID | `uuid_value` | 16 bytes, big-endian canonical order |

- Send ObjectIds in `id_value`, not `string_value`.
- Send UUIDs as 16 bytes, not text.
- Send dates as ticks, not ISO strings.
- Include the array element type even for an empty array.
- Treat an unset `Value` and `null_value` as NULL.
- Take each column type from the schema message, not from the first non-null value.

## Query streams

```text
QueryStreamMessage(schema)        exactly once, even for zero rows
QueryStreamMessage(row) ...       row.values[i] belongs to schema.columns[i]
[QueryStreamMessage(cache_metadata)]
[QueryStreamMessage(routing_advice)]
```

Replay a streamed query automatically only while the caller has seen no row.

## `BatchExecute`

- Each request has `request_id`, `kind`, and `request` (a `SqlRequest`).
- Kinds: `QUERY`, `NON_QUERY`, `START`, `COMMIT`, `ROLLBACK`, `PREPARE`, `CLOSE`. Negotiated streams add `FRAME` and `COMMIT_IF_OK`.
- Responses can interleave and arrive out of order. Match them by `request_id`.
- A query emits `schema`, rows, then `query_complete`. A failure emits `BatchError { code, message }`.
- Put every operation of one transaction on the same stream. Autocommit work can use any stream.
- Wait for the `START` reply before you send its statements, unless frame version 2 is negotiated.
- A transaction started inside a stream rolls back when the stream ends without a commit.
- Frames: send `kind = FRAME` only after the `camusdb-batch-frames` response header appears. Max 256 items and 1 MiB per frame.

## Prepared statements on gRPC

- `PREPARE (database, sql)` returns `PrepareReply { statement_id, parameter_names }`.
- Execute with `statement_id` and `positional_parameters`. Omit `sql`, `database`, and `parameters`.
- A handle lives only on its stream. Unary RPCs reject handles.
- Wait for the `PrepareReply` before you execute with its id.
- On `CADB0520`, prepare again on the current stream and replay once.

## Errors

- Unary and server-stream calls return a gRPC status. Trailers carry `camus-error-code` and `camus-error-message`.
- Batch operations return `BatchError` inside the stream.
- Auth: `UNAUTHENTICATED` for a bad or expired token, `PERMISSION_DENIED` for a missing privilege, `RESOURCE_EXHAUSTED` for login rate limits.
- Bad client input, including SQL syntax errors, maps to `INVALID_ARGUMENT`.

| Code | Retry rule |
| --- | --- |
| `CADB0502`, `CADB0504`, `CADB0505` | Replay the whole transaction from a fresh `START`. |
| `CADB0509` | Retry the same `COMMIT` or `ROLLBACK` on the same handle. |
| `CADB0520` | Prepare again, replay once. |
| `CADB0550` | `RETURNING` reply too large; use `ExecuteQuery` streaming or discard rows. |

An open `BatchExecute` stream survives ordinary token expiry. It ends with `CADB0516` on revocation. Log in again and open a new stream.
