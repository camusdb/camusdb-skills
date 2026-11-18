# CamusDB HTTP (REST/JSON) API

Read this file before you write a raw HTTP client in any language, or a curl script.
The docs call this page the HTTP/JSON protocol reference. It is for driver authors, custom clients
in languages without a driver, health probes, and endpoint diagnostics. Application code in
.NET, TypeScript, or Python uses its driver, which handles encoding, tokens, transaction handles,
and retries.
Source docs: https://camusdb.github.io/docs/http-api, https://camusdb.github.io/docs/prepared-statements, and https://camusdb.github.io/docs/sql-authentication

## Contents

- Basics
- Request limits
- Authentication and tokens
- `ColumnValue` encoding
- `JSONB` values
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
- The SQL query, streaming-query, non-query, and DDL endpoints return `CADB0400` for a malformed
  JSON body, including an escaped lone surrogate half. The message gives the JSON path and byte
  position. Administrative endpoints do not all have this check yet.
- Valid JSON with invalid SQL text or parameters follows the engine rules. A lone surrogate half in
  a SQL string parameter fails with `CADB0400` before execution.
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
{ "type": 7, "bytesValue": "AAAAAAAAgD8=" }
```

- Convert Unix milliseconds to ticks: `ticks = 621355968000000000 + unix_millis * 10000`.
- `NUMERIC` rounds to nine fraction digits. Out-of-range values fail with `CADB0417`.
- SQL byte literals use `X'...'`. JSON uses base64.
- A vector parameter is a `BYTES` value: the packed `float32` elements as base64, with `type` 7. The last example above is the vector `[0.0, 1.0]`.
- A float parameter accepts the strings `"Infinity"`, `"-Infinity"`, and `"NaN"` in `floatValue`.

## `JSONB` values

- A parameter is `{ "type": 13, "strValue": "<json text>" }`. A plain string written to a `jsonb` column also works.
- Invalid JSON fails with `CADB0418`. Nesting deeper than 512 levels fails with `CADB0419`.
- In result rows, a `jsonb` column has type `13`. By default, each cell is the document as native JSON. Numbers keep all their digits.
- In this default form, JSON `null` and SQL `NULL` are both the token `null`. A client cannot tell them apart. If it writes the value back, it can store SQL `NULL` in place of the document.

Send `"jsonbAsText": true` to keep them apart. It applies to `/execute-sql-query`, `/execute-sql-query-stream`,
and the `RETURNING` rows of `/execute-sql-non-query`. Each `jsonb` cell is then a JSON string that holds the
document text. SQL `NULL` stays the token `null`.

| Value | Default form | Text form |
| --- | --- | --- |
| SQL `NULL` | `null` | `null` |
| JSON `null` | `null` | `"null"` |
| `"abc"` | `"abc"` | `"\"abc\""` |
| `{"a": 1}` | `{"a": 1}` | `"{\"a\": 1}"` |

The server marks each column that uses the text form: `{ "name": "payload", "type": 13, "jsonbAsText": true }`.
Decode a cell as text only when its column has this mark. An older server ignores the field and sends the
default form. A string document such as `"abc"` is valid in both forms, so the mark is the only safe test.

## Endpoint list

| Method and path | Purpose |
| --- | --- |
| `GET /ping`, `GET /health` | Liveness; returns `status` and `dateTime`. |
| `GET /v1/admin/stats` | Disk, CPU, and memory of this node. Needs superuser or `MONITOR` with authentication on. |
| `POST /login`, `POST /logout` | Token exchange and revocation. |
| `POST /create-db` | `{ databaseName, ifNotExists }` |
| `POST /drop-db` | `{ databaseName }` |
| `POST /close-db` | `{ databaseName }` |
| `POST /create-table` | Structured table definition. Prefer SQL DDL. Foreign-key `onDelete` and `onUpdate` accept `"no action"` and `"restrict"`; `cascade`, `set null`, or `set default` returns `CADB0533`. |
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
| jsonb | the document as native JSON; with `jsonbAsText`, a JSON string that holds the text form. See "`JSONB` values". |
| null | `null` |

Optional extras: `cacheStatus`, `cacheName`, `cacheBypassReason`, `ageMs` for cache-hinted queries, and `routing` when routing was negotiated.

### `/execute-sql-non-query` response

```json
{ "status": "ok", "rows": 1 }
```

With `RETURNING`, the response adds `columns` and positional `returningRows`. `rows` stays the affected count.
The response can also carry `warning`, as on `/execute-sql-ddl`. An `INSERT ... SELECT` time-travel copy that reads no rows sets it.

### `/execute-sql-ddl` response

```json
{ "status": "ok", "rows": 2, "warning": null }
```

- `rows` is the number of rows the statement wrote, such as the rows that `CREATE TABLE ... AS SELECT` copied. It is `0` for a DDL statement that writes no rows.
- `warning` is `null` unless there is something to report. A time-travel copy that reads no rows returns a warning.

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

A prepared statement registers SQL once. Later executions send a handle and positional values.
Preparable: `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `SHOW`. `/execute-sql-ddl` rejects a handle; it does not ignore it.

### `POST /prepare-sql-statement`

```json
POST /prepare-sql-statement
{ "databaseName": "app", "sql": "UPDATE robots SET name = @name, year = @year WHERE id = @id" }

200 { "status": "ok", "statementId": "opaque-node-local-handle", "parameterNames": ["@name", "@year", "@id"] }
```

- `parameterNames` lists the distinct placeholder names in binding order, first occurrence first. Each name keeps its `@`.
- A name that appears more than once occupies one slot. The list includes placeholders inside subqueries.
- Preparing parses and registers the SQL, but checks no privileges. Authorization runs at each execution, for the principal of that request.
- A statement that does not parse fails at prepare, not at a later execution.

### Execute a prepared statement

Send `statementId` and `positionalParameters` to `/execute-sql-query`, `/execute-sql-query-stream`, or `/execute-sql-non-query`:

```json
{
  "statementId": "opaque-node-local-handle",
  "positionalParameters": [
    { "type": 3, "strValue": "Artoo" },
    { "type": 2, "longValue": 1977 },
    { "type": 1, "strValue": "507f1f77bcf86cd799439011" }
  ]
}
```

- `positionalParameters[i]` binds to `parameterNames[i]`. The number of values must equal the number of names.
- Each value uses the same `ColumnValue` encoding as the inline `parameters` map.
- The names do not travel on the wire. A client that binds by name maps its arguments to the ordinals itself.
- Omit `sql`, `databaseName`, and `parameters`. Every other field works as it does inline, including `txnIdPT`, `txnIdCounter`, `isolationLevel`, `transactionMode`, `locking`, and `causalToken`.

### `POST /close-sql-statement`

```json
{ "statementId": "opaque-node-local-handle" }
```

- Repeating a close is harmless.
- You can skip the close. The idle handle reaper collects abandoned handles. A client that prepares an unbounded number of different statements still reaches its cap.
- `CADB0521` (HTTP 429) means a prepared-statement cap was reached. Close unused handles. The defaults are 512 live statements per principal on one node, and 8192 per node.

### Handle lifetime

- A REST handle is an opaque string. It belongs to the node and the principal that prepared it.
- CamusDB frees it at an explicit close, at the idle timeout (`prepared_statement_idle_timeout_ms`, 600000 ms by default), or at process exit.
- CamusDB never replicates a handle to another node.

### Recover from `CADB0520`

An execution fails with `CADB0520` (`UnknownPreparedStatement`, HTTP 404) when the node does not know its handle.
Treat it as routine: prepare the statement again, then replay the execution once. It occurs when:

- The handle stayed unused past `prepared_statement_idle_timeout_ms`.
- The node restarted.
- A load balancer sent the request to a different node.
- The handle belongs to a different principal. CamusDB reports this case the same way on purpose, so a caller cannot confirm that someone else's handle exists.

Behind a load balancer, use sticky sessions, or accept one new prepare per node per statement.
On `/execute-sql-query-stream`, the server resolves the handle before it writes any bytes. The 404 arrives as a normal JSON error, not in the middle of a stream.

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
| `CADB0520` | Unknown prepared statement. HTTP 404. Prepare again, then replay once. |
| `CADB0521` | Prepared-statement cap reached. HTTP 429. Close unused handles. |
| `CADB0847` | Snapshot too old: retention removed history the read needs. HTTP 409. Do not retry the same snapshot. Use a newer timestamp or a new transaction. |
| `CADB0516` | Log in again. |
| `CADB0517` | Missing privilege. Do not retry. |

Switch on `code`, never on `message`. Many domain errors use HTTP 500. Authentication, authorization, validation, not-found, conflict, and rate-limit errors use their matching status.
Foreign-key violations use HTTP 409. Invalid foreign-key definitions use HTTP 400.

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
