# CamusDB HTTP (REST/JSON) API

Read this file before you write a raw HTTP client in any language, or a curl script.
Source docs: https://camusdb.github.io/docs/http-api and https://camusdb.github.io/docs/sql-authentication

## Contents

- Basics
- Request limits
- Authentication and tokens
- `ColumnValue` encoding
- Endpoint list
- SQL execution and response shapes
- Streaming queries
- Explicit transactions
- Prepared statements
- Direct row operations
- Errors and retries
- curl walkthrough

## Basics

- Every request and response property uses camel case.
- SQL, transaction, and row endpoints use `POST` with `Content-Type: application/json`.
- `GET /ping` and `GET /health` answer without a credential. `GET /v1/admin/stats` returns node stats.
- HTTP/1.1 and HTTP/2 work. HTTP/3 works on the HTTPS port when `http3_enabled: true`.
- Success bodies carry `"status": "ok"`. Failures carry `"status": "failed"`, `code`, and `message`.
- Create a database before any table, DML, or query uses it.
- Autocommit SQL runs Serializable by default.
- Cancelling the HTTP request cancels the server-side query.

## Request limits

Each limit applies per node. An operator can change it with `SET CLUSTER SETTING`. A value of `0` or less disables it.

| Setting | Default | Refusal |
| --- | --- | --- |
| `max_buffered_result_rows` | `1000000` | `CADB0556`, HTTP 400 |
| `max_buffered_result_bytes` | 256 MiB (estimate) | `CADB0556`, HTTP 400 |
| `max_statement_bytes` | 8 MiB of SQL text | `CADB0557`, HTTP 413 |
| `max_open_explicit_transactions` | `10000` | `CADB0558`, HTTP 503 |
| `max_client_connections` | `10000` | The connection is closed |

- `CADB0556` applies to `/execute-sql-query` and `/query`. The statement rolls back, including a write with `RETURNING`.
- `/execute-sql-query-stream` holds one row at a time, so these caps do not apply to a plain read.
- The stream route buffers a write with `RETURNING` before commit, so `CADB0556` applies there.
- Bound parameter values do not count toward `max_statement_bytes`. Bind large values.
- Retry `CADB0558` after a backoff. Autocommit statements are not counted.
- There is no per-client rate limit and no statement timeout.

## Authentication and tokens

Authentication is off by default. With it on, every request needs a bearer token.

```bash
curl -s -X POST https://db.example.com/login \
  -H 'Content-Type: application/json' \
  -d '{"user":"app","password":"secret"}'
```

```json
{ "status": "ok", "token": "camus_<id>.<secret>", "expiresAtUnixMs": 1785270000000, "expiresInSeconds": 900 }
```

- Send `Authorization: Bearer camus_<id>.<secret>` on later requests.
- `POST /logout` with the same header revokes the token.
- The default lifetime is 15 minutes. There is no refresh token. Log in again before `expiresAtUnixMs`.
- Renew from `expiresInSeconds` when you do not trust the local clock.
- A password change, `DROP USER`, `/logout`, or `FLUSH SESSIONS` invalidates a token.
- Failures do not reveal whether the user, password, or token was wrong (`CADB0516`).
- CamusDB refuses credentials over plaintext except from loopback (`CADB0519`). Use HTTPS.

## `ColumnValue` encoding

Parameters, row-operation values, filters, and defaults use `ColumnValue`. `type` is the numeric enum:

| `type` | Column type | Request field |
| --- | --- | --- |
| `0` | null | none |
| `1` | id / object id | `strValue`: 24 hex characters |
| `2` | int64 | `longValue` |
| `3` | string | `strValue` |
| `4` | bool | `boolValue` |
| `5` | float64 | `floatValue` |
| `6` | float32 | `floatValue` |
| `7` | bytes | `bytesValue`: base64 |
| `8` | date | `longValue`: UTC .NET ticks at midnight |
| `9` | datetime | `longValue`: UTC .NET ticks |
| `10` | array | `arrayElementType` plus `arrayValues` (list of `ColumnValue`) |
| `11` | uuid | `strValue`: hyphenated or 32 hex characters |
| `12` | numeric | `strValue`: decimal text |
| `13` | jsonb | `strValue`: JSON text |

```json
{ "type": 3, "strValue": "R2-D2" }
{ "type": 2, "longValue": 1977 }
{ "type": 1, "strValue": "507f1f77bcf86cd799439011" }
{ "type": 11, "strValue": "550e8400-e29b-41d4-a716-446655440000" }
{ "type": 12, "strValue": "12345678901234567890.123456789" }
{ "type": 9, "longValue": 639039888000000000 }
{ "type": 10, "arrayElementType": 2, "arrayValues": [{ "type": 2, "longValue": 42 }] }
{ "type": 13, "strValue": "{\"kind\": \"login\"}" }
{ "type": 5, "floatValue": "NaN" }
```

- Convert Unix milliseconds to ticks: `ticks = 621355968000000000 + unix_millis * 10000`.
- `NUMERIC` rounds to nine fraction digits. Out-of-range values fail with `CADB0417`.
- SQL byte literals use `X'...'`. JSON uses base64.
- A float parameter accepts the strings `"Infinity"`, `"-Infinity"`, and `"NaN"` in `floatValue`.
- A plain string written to a `jsonb` column also works.
- Invalid JSON fails with `CADB0418`. Nesting deeper than 512 levels fails with `CADB0419`.

## Endpoint list

| Method and path | Purpose |
| --- | --- |
| `GET /ping`, `GET /health` | Liveness; returns `status` and `dateTime`. |
| `GET /v1/admin/stats` | Disk, CPU, and memory of this node. Needs superuser or `MONITOR` with authentication on. |
| `POST /login`, `POST /logout` | Token exchange and revocation. |
| `POST /create-db` | `{ databaseName, ifNotExists }` |
| `POST /drop-db` | `{ databaseName }` |
| `POST /close-db` | `{ databaseName }` |
| `POST /create-table` | Structured table definition. Prefer SQL DDL. |
| `POST /execute-sql-ddl` | Schema SQL. Server-level statements can omit `databaseName`. |
| `POST /execute-sql-query` | `SELECT`, `SHOW`, and writes with `RETURNING`. Buffered. |
| `POST /execute-sql-query-stream` | Same body; NDJSON streaming response. |
| `POST /execute-sql-non-query` | `INSERT`, `UPDATE`, `DELETE`. |
| `POST /start-transaction` | Returns `txnIdPT` and `txnIdCounter`. |
| `POST /commit-transaction`, `POST /rollback-transaction` | Finalize by id. |
| `POST /prepare-sql-statement`, `POST /close-sql-statement` | Prepared statements. |
| `POST /insert`, `/query`, `/query-by-id`, `/update`, `/delete` | Direct row operations. |

## SQL execution and response shapes

Request fields for the three SQL endpoints:

| Field | Notes |
| --- | --- |
| `databaseName` | Target database. |
| `sql` | Statement text with `@name` placeholders. |
| `parameters` | Map from `"@name"` to `ColumnValue`. |
| `txnIdPT`, `txnIdCounter` | Resume an explicit transaction. |
| `isolationLevel` | `"Serializable"` or `"ReadCommitted"` (autocommit only). |
| `transactionMode` | `"ReadWrite"` or `"ReadOnly"` (autocommit only). |
| `locking` | `"Pessimistic"` or `"Optimistic"` (autocommit writes). |
| `priority` | `"Background"`, `"Low"`, `"Normal"`, `"High"`, `"Critical"`. |
| `causalToken` | Token of your last write. An autocommit query reads at a timestamp at least this value. Other statements ignore it. |
| `jsonbAsText` | `true` writes each `jsonb` cell as a JSON string that holds the document text. Applies to query, stream, and `RETURNING` rows. |
| `routingAcceptVersion` | `1` opts in to routing advice. |
| `statementId`, `positionalParameters` | Prepared execution. Omit `sql`, `databaseName`, `parameters`. |
| `discardReturningRows` | Non-query only: return just the count. |

The server ignores isolation, mode, locking, and priority when the request resumes a transaction.

### `/execute-sql-query` response

The server returns a column list and positional rows. Read row cell `c` against `columns[c]`.

```json
{
  "status": "ok",
  "total": 1,
  "columns": [{ "name": "id", "type": 1 }, { "name": "name", "type": 3 }],
  "rows": [["507f1f77bcf86cd799439011", "R2-D2"]],
  "serverTimeMs": 1.2
}
```

A response can also carry a `causalToken` object with `n`, `l`, and `c`. Send it back unchanged on the next autocommit query or `/start-transaction`.
An explicit transaction's floor is fixed at its start. A token on a later statement does not advance it.

Positional cell encoding:

| Column type | JSON cell |
| --- | --- |
| id, string | string |
| int64 | number (may exceed 2^53; parse as big integer) |
| date, datetime | number of UTC .NET ticks |
| bool | boolean |
| float64, float32 | number; a non-finite value is the string `"Infinity"`, `"-Infinity"`, or `"NaN"` |
| bytes | base64 string |
| uuid | `[high, low]` pair of signed 64-bit integers |
| numeric | decimal string |
| array | JSON array of element values; `columns[c].elementType` names the element type |
| jsonb | the document as native JSON; with `jsonbAsText`, a JSON string that holds the text form |
| null | `null` |

By default, JSON `null` and SQL `NULL` are both the token `null`. Send `"jsonbAsText": true` to keep them apart.
The server then marks each such column: `{ "name": "payload", "type": 13, "jsonbAsText": true }`.
Decode a cell as text only when its column has this mark. An older server ignores the field and sends native JSON.

Optional extras: `cacheStatus`, `cacheName`, `cacheBypassReason`, `ageMs` for cache-hinted queries, and `routing` when routing was negotiated.

### `/execute-sql-non-query` response

```json
{ "status": "ok", "rows": 1 }
```

With `RETURNING`, the response adds `columns` and positional `returningRows`. `rows` stays the affected count.

## Streaming queries

`POST /execute-sql-query-stream` returns `application/x-ndjson`:

```json
{"status":"ok","columns":[{"name":"id","type":1},{"name":"name","type":3}]}
["507f1f77bcf86cd799439011","R2-D2"]
["507f1f77bcf86cd799439012","C-3PO"]
{"status":"ok","total":2,"serverTimeMs":3.1}
```

- Line 1 is the schema header. Each next line is one positional row. The last line is the trailer.
- An error before the first line returns a normal JSON error with an HTTP error status.
- An error after streaming starts arrives in the trailer, with HTTP 200 already sent: `{"status":"failed","code":"CADB0502",...}`. Always check the trailer.
- Autocommit streaming runs one attempt. Use the buffered endpoint when automatic server retry matters.
- The trailer can carry an optional causal token.

## Explicit transactions

```json
POST /start-transaction
{ "databaseName": "app", "isolationLevel": "Serializable", "transactionMode": "ReadWrite", "locking": "Pessimistic" }

200 { "status": "ok", "txnIdPT": 123, "txnIdCounter": 1 }
```

- Pass `txnIdPT` and `txnIdCounter` on each SQL or row request of the transaction.
- Commit or roll back with `{ databaseName, txnIdPT, txnIdCounter }`.
- `/start-transaction` accepts an optional `causalToken`. Send the token of your last commit. It is the transaction's floor.
- A successful commit returns `causalToken` when it has a commit timestamp. It can be `null`. Carry it into the next `/start-transaction` or autocommit query.
- A failed statement rolls the transaction back at once. A later statement or commit fails with `CADB0400` ("Unknown transaction").
- `/rollback-transaction` after that failure succeeds and does nothing. Start a new transaction to continue.
- A begin past the node cap fails with `CADB0558`, HTTP 503.
- Send every request of a transaction to the node that started it. Other nodes reject the handle.
- Defaults: Serializable, read-write, pessimistic, normal priority.
- An idle transaction rolls back after `transaction_idle_timeout_ms` (5 minutes by default).
- `ROLLBACK` is idempotent by id. A finalize still in progress returns `CADB0501`.

## Prepared statements

```json
POST /prepare-sql-statement
{ "databaseName": "app", "sql": "SELECT id, name FROM robots WHERE year >= @year" }

200 { "status": "ok", "statementId": "opaque-handle", "parameterNames": ["@year"] }
```

```json
POST /execute-sql-query
{ "statementId": "opaque-handle", "positionalParameters": [{ "type": 2, "longValue": 1980 }] }
```

- `positionalParameters[i]` binds to `parameterNames[i]`. The counts must match.
- A repeated name occupies one slot.
- Handles belong to one node and one principal. Close with `POST /close-sql-statement { "statementId": ... }`.
- On `CADB0520` (HTTP 404), prepare again and replay once. Behind a load balancer, expect one prepare per node.
- `CADB0521` (HTTP 429) means a prepared-statement cap was reached. Close unused handles.
- Preparable: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `SHOW`.

## Direct row operations

- Filters: `{ "columnName": "year", "op": ">=", "value": <ColumnValue> }`. `op` is a comparison string such as `=`, `>=`, or `<`.
- Order: `{ "columnName": "year", "type": 1 }` where `0` is ascending and `1` is descending.
- `/insert`: `{ databaseName, tableName, values: { col: ColumnValue } }`.
- `/query`: `{ databaseName, tableName, filters, orderBy }`.
- `/query-by-id`: `{ databaseName, tableName, id }`.
- `/update`: `{ databaseName, tableName, values, filters }`.
- `/delete`: `{ databaseName, tableName, filters }`.
- `/update` and `/delete` need at least one filter. Missing or empty filters fail with `CADB0400`, HTTP 400. An explicit transaction is aborted too.
- To change every row on purpose, use SQL with `WHERE TRUE`.
- `values` in `/insert` and `/update` must name each column once. A repeated key, including names that differ only in case, fails with `CADB0403`, HTTP 400, before any write.

Prefer SQL endpoints. They share the parser and executor with the engine tests.

## Errors and retries

| Code | Action |
| --- | --- |
| `CADB0502`, `CADB0504`, `CADB0505` | Replay the whole transaction from a new `/start-transaction`. |
| `CADB0509` | Send the same commit or rollback again for the same `txnIdPT`/`txnIdCounter`. |
| `CADB0400` | Bad input. With "Unknown transaction", a statement already failed. Start a new transaction. |
| `CADB0403` | A column was named twice. Do not retry. |
| `CADB0555` | `FOR UPDATE NOWAIT` met a held row. HTTP 409. The transaction ended. Do not replay automatically. |
| `CADB0556` | Buffered result too large. HTTP 400. The statement rolled back. Stream or page. |
| `CADB0557` | SQL text too large. HTTP 413. Bind large values. |
| `CADB0558` | Node at a concurrency cap. HTTP 503. Retry after a backoff. |
| `CADB0506` | Do not retry. Split the transaction. |
| `CADB0503` | Retry later or on another node. |
| `CADB0520` | Prepare again, then replay once. |
| `CADB0516` | Log in again. |
| `CADB0517` | Missing privilege. Do not retry. |

Switch on `code`, never on `message`. Many domain errors use HTTP 500. Authentication, authorization, validation, not-found, conflict, and rate-limit errors use their matching status.

## curl walkthrough

```bash
BASE=http://localhost:5095
H='Content-Type: application/json'

curl -s -X POST $BASE/create-db -H "$H" -d '{"databaseName":"app","ifNotExists":true}'

curl -s -X POST $BASE/execute-sql-ddl -H "$H" -d '{"databaseName":"app",
  "sql":"CREATE TABLE IF NOT EXISTS players (id UUID PRIMARY KEY NOT NULL DEFAULT (gen_uuid_v4()), name STRING(64) NOT NULL)"}'

curl -s -X POST $BASE/execute-sql-non-query -H "$H" -d '{"databaseName":"app",
  "sql":"INSERT INTO players (name) VALUES (@name)",
  "parameters":{"@name":{"type":3,"strValue":"ada"}}}'

TX=$(curl -s -X POST $BASE/start-transaction -H "$H" -d '{"databaseName":"app"}')
PT=$(echo "$TX" | jq .txnIdPT); CT=$(echo "$TX" | jq .txnIdCounter)

curl -s -X POST $BASE/execute-sql-non-query -H "$H" -d "{\"databaseName\":\"app\",
  \"txnIdPT\":$PT,\"txnIdCounter\":$CT,
  \"sql\":\"UPDATE players SET name = @n WHERE name = @o\",
  \"parameters\":{\"@n\":{\"type\":3,\"strValue\":\"grace\"},\"@o\":{\"type\":3,\"strValue\":\"ada\"}}}"

curl -s -X POST $BASE/commit-transaction -H "$H" -d "{\"databaseName\":\"app\",\"txnIdPT\":$PT,\"txnIdCounter\":$CT}"
```

Add `-H "Authorization: Bearer $TOKEN"` to every call when authentication is on.
