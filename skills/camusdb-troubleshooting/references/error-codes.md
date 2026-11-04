# CamusDB error codes

Read this file when you have a `CADBxxxx` code. Find the row, explain the
meaning, and apply the fix. Source: https://camusdb.github.io/docs/error-codes

## Retry column legend

| Value | Meaning |
| --- | --- |
| `Replay` | Retryable. Roll back and replay the whole transaction from `BEGIN`, with backoff. |
| `Yes` | Retryable as is, usually after a short wait or on another node. |
| `After fix` | Retry only after an outside condition changes, such as fresh credentials or free disk. |
| `Finalize` | Resend the same `COMMIT` or `ROLLBACK` for the same transaction id. Never replay. |
| `No` | Change the request, the data, or the schema first. |
| `Operator` | Needs operator investigation. A blind retry rarely helps. |

A dagger (†) marks a code that the docs' retry guidance does not classify. The
value comes from the code's documented meaning.

## Where the code appears

- HTTP: `{"status": "failed", "code": "CADB0400", "message": "..."}`. A streaming
  response can fail after HTTP `200`; the code is in the last trailer line.
- gRPC: the `camus-error-code` trailer. A batch operation carries it in
  `BatchError`.
- .NET driver: `CamusException.Code`. Engine: `CamusDBException`.

## CADB00xx: catalog, metadata, storage state

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0010` | `DatabaseDoesntExist` | Database never created, dropped, renamed away, or (with auth) the caller has no grant on it. HTTP 404. | Create the database explicitly, fix the name, or grant access. | No |
| `CADB0011` | `TableDoesntExist` | The table does not exist, or the name is empty. | Fix the name or create the table. | No† |
| `CADB0012` | `DatabaseAlreadyExists` | `CREATE DATABASE` or a rename targets an existing name. | Pick another name. | No |
| `CADB0013` | `TableAlreadyExists` | `CREATE TABLE` or `RENAME TO` targets an existing table. | Pick another name, or use `CREATE TABLE IF NOT EXISTS`. | No† |
| `CADB0014` | `SystemSpaceCorrupt` | CamusDB cannot decode or trust persisted metadata, rows, schema, index, or registry data. | Collect logs. Investigate storage. Consider restore. | Operator |
| `CADB0015` | `TableCorrupt` | Reserved for table-level corruption. Rarely surfaced. | Investigate storage. | Operator† |
| `CADB0016` | `IndexDoesntExist` | Reserved for a missing index. Rarely thrown. | Fix the index name. | No† |
| `CADB0017` | `InvalidIndexLayout` | Reserved for an invalid persisted index layout. | Investigate storage. | Operator† |
| `CADB0018` | `DatabaseNameReserved` | Name such as `_system` or `information_schema`. | Pick another name. | No |
| `CADB0019` | `DatabaseCreationIncomplete` | Reserved for older standalone layouts. Not expected today. | Investigate. | Operator† |
| `CADB00297` | `InvalidPageOffset` | Reserved, low-level page offset. Note the five-digit code. | Investigate storage. | Operator† |
| `CADB0096` | `InvalidInformationSchema` | Reserved, invalid information-schema state. | Investigate. | Operator† |
| `CADB0097` | `InvalidPageLength` | Reserved, low-level page length. | Investigate storage. | Operator† |
| `CADB0098` | `InvalidPageChecksum` | Reserved, low-level checksum mismatch. | Investigate storage. | Operator† |
| `CADB0099` | `InvalidInternalOperation` | Unexpected internal state: planner, replicated index shape, row vanished during update, forwarder response. | Capture the statement and logs. Report it. | Operator |

## CADB03xx: integrity constraints

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0300` | `DuplicateUniqueKeyValue` | Insert, update, or index backfill violates a unique index or key. | Change the value, or update the existing row. | No |
| `CADB0301` | `NotNullViolation` | `NULL` into a `NOT NULL` column. | Supply a value or a default. | No |
| `CADB0302` | `ValueTooLong` | `STRING` or `BYTES` longer than the column maximum. | Shorten the value or widen the column. | No |
| `CADB0303` | `CheckConstraintViolation` | A row violates a `CHECK`, or the check hits incompatible values or a regex failure. | Fix the data or the constraint. | No |
| `CADB0304` | `ForeignKeyViolation` | Child row has no parent, or `ADD CONSTRAINT ... FOREIGN KEY` finds orphans. | Insert the parent first, or fix orphans. | No† |
| `CADB0305` | `ForeignKeyRestrictDelete` | `DELETE` removes a parent key still referenced. | Delete or repoint children first. | No† |
| `CADB0306` | `ForeignKeyRestrictUpdate` | `UPDATE` changes a parent key still referenced. | Update children first, or keep the key. | No† |

## CADB04xx: invalid SQL or input

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0400` | `InvalidInput` | Invalid request shape: missing names, bad DDL/DML parameters, bad casts, malformed UUID, duplicate aliases, invalid transaction priority, invalid TTL options, bad `INSERT ... SELECT` column count, bad `GROUP BY`/`HAVING`/`DISTINCT`. Also `CREATE TABLE` without a primary key. A comparison, or a `CASE` or `COALESCE` result, across types with no rule. A non-integer parameter bound to `LIMIT` or `OFFSET`. A `FOR UPDATE` shape or transaction that is refused. A typed `/update` or `/delete` with no filter. `Unknown transaction`: a handle used after a failed statement or an idle timeout, or on another node. HTTP 400. | Read the message. Fix the request. For `Unknown transaction`, start a new transaction. | No |
| `CADB0401` | `UnknownType` | A type CamusDB cannot handle in that context. | Use a supported type or cast. | No† |
| `CADB0402` | `DuplicatePrimaryKey` | Reserved. Duplicates usually report `CADB0300`. | As for `CADB0300`. | No† |
| `CADB0403` | `DuplicateColumn` | Same column name twice, ignoring case: in a schema, an `INSERT` column list, an `UPDATE` or `ON CONFLICT` `SET` list, or a typed row write. Also a rename to an existing column. Refused before any write. HTTP 400. | Remove or rename the repeated column. | No† |
| `CADB0404` | `UnknownColumn` | Column not present or not visible. | Fix the name. Check quoting. | No |
| `CADB0405` | `UnknownKey` | Planner or scanner got a key shape it cannot map. Uncommon; usually internal. | Capture and report. | Operator† |
| `CADB0406` | `SqlSyntaxError` | The parser cannot parse the text. | Fix the SQL. Use backticks for reserved-word identifiers. | No |
| `CADB0407` | `InvalidAstStmt` | Parsed, but the shape is unsupported or unusable. | Rewrite the statement. | No† |
| `CADB0408` | `SchemaLimitExceeded` | Name longer than `max_identifier_length`, or over `max_columns_per_table`, `max_indexes_per_table`, `max_tables_per_database`, or `max_databases`. | Shorten or reduce. | No |
| `CADB0409` | `InvalidAsOfSystemTime` | Malformed, future, or non-positive time; bad parameter; or used inside an explicit transaction. | Use a past time in autocommit. | No |
| `CADB0410` | `MalformedVector` | Vector `BYTES` not a whole number of 4-byte floats, or empty. | Fix the encoding. | No |
| `CADB0411` | `VectorDimensionMismatch` | Distance operands differ in dimension. | Use matching dimensions. | No |
| `CADB0412` | `InvalidVectorValue` | `NaN` or infinity, or zero vector in `cosine_distance`. | Clean the vector. | No |
| `CADB0413` | `StatementTooDeeplyNested` | Parse tree too deep. | Reduce nesting or split. | No |
| `CADB0414` | `ColumnStorageNotApplicable` | `STORAGE` on a type without variable-length values. | Use it only on `STRING`, `BYTES`/`BLOB`, `JSONB`, `ARRAY`. | No† |
| `CADB0415` | `InvalidForeignKeyDefinition` | FK target invalid: column count or type, no PK or unique index, a view, or another database. | Fix the reference. | No† |
| `CADB0416` | `ForeignKeyCycle` | FK would create a cycle across tables. Self-reference is allowed. | Remove the cycle. | No† |
| `CADB0417` | `NumericValueOutOfRange` | `NUMERIC` outside ±99999999999999999999999999999.999999999. | Scale the value. | No† |
| `CADB0418` | `InvalidJsonText` | Text for a `JSONB` value is not storable: invalid JSON, a `\u0000` escape or lone surrogate, or a number past the PostgreSQL `numeric` limits. HTTP 400. | Fix the text. | No† |
| `CADB0419` | `JsonNestingTooDeep` | A `JSONB` document nests deeper than 512 levels. HTTP 400. | Reduce the nesting. | No† |
| `CADB0420` | `IntegerValueOutOfRange` | `INT64` result outside its range: arithmetic, unary minus, a `SUM` total, a literal, or a conversion. The statement stores nothing. HTTP 400, gRPC `OUT_OF_RANGE`. | Use `NUMERIC` for a wider exact value, or fix the data. | No† |
| `CADB0421` | `FloatValueOutOfRange` | A float result from finite inputs overflows or underflows, or a `FLOAT64` value does not fit `FLOAT32`. HTTP 400, gRPC `OUT_OF_RANGE`. | Scale the values, or use `NUMERIC`. | No† |
| `CADB0422` | `JsonbValueTooLarge` | `jsonb_agg` or `jsonb_object_agg` built a value over 268,435,455 bytes. HTTP 400. | Reduce the group size. | No† |

## CADB05xx: transactions, schema catch-up, prepared statements, auth, objects

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0501` | `TransactionAlreadyCompleted` | Commit or rollback on a transaction that is no longer active, or Kahuna returned a permanent commit failure. | Do not resend. Start a new transaction if the work is still needed. | No† |
| `CADB0502` | `TransactionConflict` | Lock conflict, conflicting concurrent write, or `FOR UPDATE` on a held row. Also a table that gained an index or constraint, or changed its row layout, under the transaction. Message samples contended keys, or names the commit refusal class. | Replay. Shorten transactions. Shard hot rows. | Replay |
| `CADB0503` | `SchemaCatchingUp` | Node is more than one schema version behind; it rejects reads and DML for that database. | Retry later or on another node. | Yes |
| `CADB0504` | `TransactionMustRetry` | Transient start, admission, routing, leader, transport, lock-wait, replication-wait, pre-write storage conflict, unservable read range, a read that waited too long for a commit, or a schema leader change during DDL. | Replay with backoff. Check admission and leadership. | Replay |
| `CADB0505` | `TransactionLifetimeExceeded` | Serializable read-write transaction passed its lifetime (1 hour default). | Roll back. Replay. Shorten it. | Replay |
| `CADB0506` | `TransactionMutationLimitExceeded` | Would exceed 20,000 row/index mutations. HTTP 400. | Split into batches. Use `TRUNCATE` to empty a table. | No |
| `CADB0507` | `SpillStorageUnavailable` | Cannot create the spill directory or open a spill file. | Free disk, fix permissions under `data_dir`, or use another node. | No |
| `CADB0508` | `DatabaseHasLiveDescendants` | `DROP DATABASE` on a database with live branches. | Drop the branches first. | No |
| `CADB0509` | `TransactionFinalizeUnresolved` | Commit or rollback has no terminal answer after bounded retries. It may have committed. | Resend the same finalize for the same id. | Finalize |
| `CADB0510` | `OrphanNotFound` | `RELINK TO` or reclamation names an unknown, recovered, or reclaimed orphan id. | Check the orphan id. | No |
| `CADB0511` | `CommentTooLong` | Comment over 65,535 characters. | Shorten it. | No |
| `CADB0512` | `UserAlreadyExists` | `CREATE USER` on an existing user. | Use `IF NOT EXISTS`. | No |
| `CADB0513` | `UserDoesNotExist` | `ALTER`/`DROP USER`, `GRANT`, `REVOKE` on a missing user. `GRANT` never creates users. | Create the user first. | No |
| `CADB0514` | `UnsupportedAuthPlugin` | Plugin other than `sha256_password`. | Use `sha256_password`. | No |
| `CADB0515` | `InvalidPrivilege` | Unknown privilege, or invalid for the scope. | Fix the `GRANT`/`REVOKE`. | No |
| `CADB0516` | `AuthenticationFailed` | Credentials missing, invalid, expired, revoked, or rejected. HTTP 401. | Obtain fresh credentials. | After fix |
| `CADB0517` | `InsufficientPrivilege` | Authenticated, but lacks the privilege. HTTP 403. `SHOW ENGINE STATS`, `SHOW VARIABLES`, `SHOW SLOW QUERIES` need a superuser. | Grant the privilege or use a superuser. | No |
| `CADB0518` | `TooManyAuthAttempts` | Login rate limit or hash concurrency guard. | Wait for the rate-limit window. | After fix |
| `CADB0519` | `InsecureTransport` | Credentials over plaintext while auth requires TLS. | Use HTTPS, or a trusted hop with `AllowInsecureCredentials`. | No |
| `CADB0520` | `UnknownPreparedStatement` | Handle not on this node, stream, or principal: expired, closed, other stream, other node, or restart. | Prepare again and replay once. | Yes |
| `CADB0521` | `PreparedStatementLimitExceeded` | Count cap, byte budget, or size cap exceeded. | Close handles, reduce distinct SQL shapes, or tune limits. | No |
| `CADB0522` | `AnalyzeRequiresNoPendingWrites` | `ANALYZE` inside a transaction with uncommitted writes. | Commit or roll back first. | No |
| `CADB0523` | `ViewDoesntExist` | Missing view, or `SHOW STATISTICS FOR` a plain view. | Fix the name; query base tables' statistics. | No |
| `CADB0524` | `ViewAlreadyExists` | Name taken by a view or materialized view. | Pick another name. | No |
| `CADB0525` | `ViewNotUpdatable` | DML on a non-updatable view or a materialized view. | Write to the base table. | No |
| `CADB0526` | `ViewColumnNotUpdatable` | Write to a computed view column. | Write the base column. | No |
| `CADB0527` | `ViewCheckOptionViolated` | Row fails the view predicate under `WITH CHECK OPTION`. | Fix the row. | No |
| `CADB0528` | `ViewRecursionDetected` | View dependencies form a cycle. | Break the cycle. | No |
| `CADB0529` | `CannotChangeViewShape` | `CREATE OR REPLACE VIEW` changes names, types, or order. Only appending is allowed. | Drop and recreate. | No |
| `CADB0530` | `DependentObjectsExist` | Change would orphan dependent views or foreign keys, including a truncate of a referenced parent. | Drop the dependent object first. | No |
| `CADB0531` | `MaterializedViewNotPopulated` | Read of a `WITH NO DATA` view never refreshed. | Run `REFRESH MATERIALIZED VIEW`. | No |
| `CADB0532` | `RefreshAlreadyInProgress` | Another refresh of the view is running. | Retry after it finishes. | Yes |
| `CADB0533` | `FeatureNotSupported` | Parsed but not implemented, such as `REFRESH ... CONCURRENTLY`, `FOR SHARE`, `FOR KEY SHARE`, or `SKIP LOCKED` at Serializable. HTTP 501. | Use the form named in the message. | No |
| `CADB0534` | `ConcurrentSchemaChange` | The definition changed under a derive-and-publish operation. Nothing applied. | Re-read the definition and run again. | Yes |
| `CADB0535` | `SequenceUnavailable` | No confirmed leader for a monotonic counter for `sequence_retry_budget_ms`. Nothing written. HTTP 503. | Run again once the election settles. | Yes |
| `CADB0536` | `InsufficientDiskSpace` | Free space below `min_free_disk_bytes`. Writes refused; reads and DDL still work. HTTP 507. | Free disk or lower the threshold. | After fix |
| `CADB0537` | `SnapshotPrecedesContentsGeneration` | `AS OF SYSTEM TIME` before a `TRUNCATE` cut. | Read at or after the cut, or `CREATE TABLE ... RELINK TO`. | No |
| `CADB0538` | `StatementNotAllowedInTransaction` | `TRUNCATE`, `ALTER TABLE ... REWRITE STORAGE`, or `ALTER SEQUENCE ... RESTART` inside an explicit transaction. The refusal aborts the transaction. | Roll back, then run it in autocommit. | No |
| `CADB0539` | `BranchSnapshotProtectionLost` | Branch lost the snapshot protection at its fork point. HTTP 410. | Recreate the branch from the parent. | No |
| `CADB0540` | `LargeValueCorrupt` | Compressed or out-of-line value failed to decompress or checksum. | Investigate storage. | Operator† |
| `CADB0541` | `LargeValueNotResolved` | Internal read decoded an unfetched out-of-line value. | Report it. | Operator† |
| `CADB0542` | `SequenceAlreadyExists` | Name held by a sequence, table, or view. | Another name or `IF NOT EXISTS`. | No† |
| `CADB0543` | `SequenceDoesntExist` | Sequence statement or function names a missing sequence. | Fix the name. | No† |
| `CADB0544` | `SequenceExhausted` | Next value would pass `MAXVALUE`. | Raise the limit: `ALTER SEQUENCE name MAXVALUE n` or `NO MAXVALUE`. | No† |
| `CADB0545` | `InvalidSequenceDefinition` | Non-positive increment, `CACHE` < 1, `MAXVALUE` < `MINVALUE`, or `START` out of range. | Fix the definition. | No† |
| `CADB0546` | `SequenceValueNotYetDefined` | `currval`/`lastval` before this transaction drew a value. | Call `nextval` first. | No† |
| `CADB0547` | `SequenceCallNotAllowedHere` | Sequence call where the draw count is unknowable, such as filters or joins. | Move the call. | No† |
| `CADB0548` | `SequenceInUse` | Owned by an identity column, or a default depends on it. | Drop or change the dependent first. | No† |
| `CADB0549` | `SpillLimitExceeded` | Spill would exceed `spill_max_total_bytes` on the node. Nothing kept. HTTP 507. | Let other queries finish, raise the limit, or reduce the spill. | After fix† |
| `CADB0550` | `ReturningResultTooLarge` | `RETURNING` rows do not fit in one gRPC non-query reply. Rolled back. | Use a streaming query endpoint, or the count-only flag. | No† |
| `CADB0551` | `QueryNotFound` | `CANCEL QUERY` found no visible query with that id. | Check the id with `SHOW QUERIES`. | No† |
| `CADB0552` | `QueryNotCancellable` | Write, schema change, or still parsing. | Wait for it to finish. | No† |
| `CADB0553` | `QueryOwnerUnreachable` | Owning node did not answer before the cancel timeout. The query may still run. | Retry the cancel. | Yes† |
| `CADB0554` | `QueryCancelled` | The statement was stopped by `CANCEL QUERY`. Rows already received are real. In an explicit transaction, it aborts the transaction. | Rerun only if intended. | No† |
| `CADB0555` | `LockNotAvailable` | `SELECT ... FOR UPDATE NOWAIT` met a held row. The transaction ended. HTTP 409, gRPC `ABORTED`. | Do not replay automatically. Let the application pick other work, or retry later. | No |
| `CADB0556` | `ResultSetTooLarge` | A buffered result passed `max_buffered_result_rows` or `max_buffered_result_bytes`. The statement rolled back, including a write with `RETURNING`. HTTP 400, gRPC `RESOURCE_EXHAUSTED`. | Use a streaming transport, or page with `LIMIT`. | No† |
| `CADB0557` | `StatementTooLarge` | Inline SQL text passed `max_statement_bytes`. Refused before the parse. HTTP 413, gRPC `INVALID_ARGUMENT`. | Bind large values as parameters, or split the statement. | No† |
| `CADB0558` | `ConcurrencyLimitExceeded` | The node reached `max_open_explicit_transactions` or `cluster_activity_max_concurrent_fan_outs`. Nothing ran. HTTP 503, gRPC `RESOURCE_EXHAUSTED`. | Retry after a back-off. Find leaked transactions. | Yes† |
| `CADB0559` | `JsonbValueCorrupt` | Stored `JSONB` bytes do not decode: damage, or a value from a newer build. HTTP 500. | Investigate storage and node versions. | Operator† |

## CADB06xx: configuration

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0600` | `InvalidConfig` | Invalid or missing config file, wrong mode, bad ports or peers, invalid transaction, locking, priority, prepared-statement, statistics, TTL, spill, large-value, diagnostics, parser-cache, or regex settings, unknown keys, or unsupported `kahuna` options. Also a `key_range_sharding` value that disagrees with the cluster's recorded routing mode (the node refuses to start). Also a `SET CLUSTER SETTING` that breaks a cross-field invariant. | Read the message; it names the setting(s). Fix and restart, or choose a valid value. | No† |

## CADB07xx: backup and point-in-time recovery

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0700` | `BackupNotConfigured` | `kahuna.backup_dir` is unset. HTTP 503. | Configure the backup directory. | No |
| `CADB0701` | `BackupChainInvalid` | No full root, a gap, a broken link, or a cycle. HTTP 422. | Inspect the chain. | Operator |
| `CADB0702` | `BackupNeedsFullBackup` | Incremental parent aged past retention. HTTP 409. | Take a full backup. | No |
| `CADB0703` | `RestorePointOutOfWindow` | Restore point outside the chain coverage. HTTP 422. | Pick a covered time. | No |
| `CADB0704` | `RestoreFailed` | Failure copying the base image or replaying WAL. HTTP 500. | Investigate logs and artifacts. | Operator |
| `CADB0705` | `BackupParentMissing` | `parentBackupId` does not exist. HTTP 404. | Use a valid parent. | No |
| `CADB0706` | `BackupCorruptArtifact` | Artifact missing, truncated, extra, duplicated, or bad digest. HTTP 422. | Investigate the backup storage. | Operator |
| `CADB0707` | `RestoreTargetConflict` | Destination exists or overlaps a protected path. HTTP 409. | Choose another target. | No |
| `CADB0708` | `BackupExactCheckpointUnavailable` | Backend cannot cut an exact checkpoint there. HTTP 409. | Choose another cut. | No† |
| `CADB0709` | `BackupUnsupportedFormat` | Legacy or unsupported manifest format. HTTP 422. | Use a supported backup. | No† |
| `CADB070A` | `BackupRetryableLeadershipLoss` | Leader lost mid-operation. Nothing durable applied. HTTP 503. | Retry once a leader is elected. | Yes |
| `CADB070B` | `BackupCancelled` | The caller cancelled. HTTP 499. | Run again if wanted. | Yes (per backup docs) |
| `CADB070C` | `RemoteRestoreDisabled` | No `kahuna.restore_root` and no unconfined opt-in. HTTP 403. | Configure `kahuna.restore_root`. | No |
| `CADB070D` | `BackupTopologyChanged` | Topology changed during a coordinated backup. Nothing published. HTTP 503. | Retry once membership is stable. | Yes |
| `CADB070E` | `BackupNotCoordinator` | Coordinated backup on a non-coordinator node. HTTP 421. | Send it to the coordinator. | No |
| `CADB070F` | `BackupInsecureRoot` | Backup or restore root is a symlink, or group/world-writable. HTTP 500. | Fix the directory and permissions. | Operator |
| `CADB0710` | `PitrBootstrapFailed` | Startup only. A joining node could not seed itself from a restored backup chain. | Copy a fresh restored data root into place and start again. | Operator† |

## CADB08xx: C# extensions

`CADB0800`-`CADB0808` and `CADB0827` stop the server at startup, except that
`CADB0807` happens at run time. They never reach a normal API caller.

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0800` | `ExtensionPathInvalid` | An extension path is relative, or is listed in both `extension_assemblies` and `unsafe_extension_assemblies`. | Use one absolute path. | Operator† |
| `CADB0801` | `ExtensionFileNotFound` | A configured extension file does not exist. | Deploy the file or fix the path. | Operator† |
| `CADB0802` | `ExtensionNotAnAssembly` | The file is not a loadable .NET assembly. | Deploy a valid build. | Operator† |
| `CADB0803` | `ExtensionContractMismatch` | Built against a newer `CamusDB.Extensions.Abstractions` than the server carries. | Rebuild the extension or upgrade the server. | Operator† |
| `CADB0804` | `ExtensionDuplicateAssembly` | Two files declare the same extension name and version. | Remove one. | Operator† |
| `CADB0805` | `ExtensionDuplicateMember` | A member name collides with a built-in, or one extension defines it twice. | Rename the member. | Operator† |
| `CADB0806` | `ExtensionVerificationFailed` | A SAFE assembly failed the static check. The message lists every finding. | Fix the extension. Move it to `unsafe_extension_assemblies` only after a review. | Operator† |
| `CADB0807` | `ExtensionAssemblyRefused` | At run time, a SAFE extension asked for an assembly outside the allow-list. HTTP 500. | Fix the extension, or review it for the unsafe list. | Operator† |
| `CADB0808` | `ExtensionInvalidSignature` | A member has a shape the server cannot bind. | Fix the signature or constructor. | Operator† |
| `CADB0809` | `ExtensionFunctionFailed` | An extension function threw. The message names it. HTTP 400. | Fix the input or the function. | No† |
| `CADB0810` | `ExtensionValueOutOfRange` | A `NUMERIC` does not fit `decimal`, or a returned `decimal` does not fit `NUMERIC`. HTTP 400. | Fix the value. | No† |
| `CADB0811` | `TriggerTypeNotFound` | `CREATE TRIGGER` names a type no loaded extension defines. HTTP 400. | Fix the type name. | No† |
| `CADB0812` | `TriggerTypeAmbiguous` | Two loaded extensions define the type. HTTP 400. | Use an unambiguous type. | No† |
| `CADB0813` | `TriggerTypeInvalid` | The type lacks the trigger interface or a public parameterless constructor. HTTP 400. | Fix the type. | No† |
| `CADB0814` | `TriggerAlreadyExists` | The table already has a trigger with that name. HTTP 409. | Pick another name. | No† |
| `CADB0815` | `TriggerNotFound` | `DROP TRIGGER` names a trigger the table does not have. HTTP 404. | Fix the name. | No† |
| `CADB0816` | `TriggerTargetInvalid` | A trigger targets a view or materialized view. HTTP 400. | Use a plain table. | No† |
| `CADB0817` | `TriggerBindingMismatch` | DML on a table whose trigger extension this node does not load, or loads with another SHA-256. HTTP 503. | Deploy the same file on every node, or use a node that has it. | After fix† |
| `CADB0818` | `TriggerFailed` | A trigger threw. The statement wrote nothing. HTTP 400. | Fix the data or the trigger. | No† |
| `CADB0819` | `ExtensionNestingTooDeep` | Triggers and procedures nested SQL past 16 levels. HTTP 400. | Remove the recursion. | No† |
| `CADB0820` | `ExtensionStatementRefused` | Extension SQL ran something other than `SELECT`, `INSERT`, `UPDATE`, `DELETE`. HTTP 400. | Remove the statement. | No† |
| `CADB0821` | `ProcedureNotFound` | `CALL` names no procedure in a loaded extension. HTTP 404. | Fix the name or install the extension. | No† |
| `CADB0822` | `ProcedureFailed` | A procedure threw. All writes of the `CALL` rolled back. HTTP 400. | Fix the input or the procedure. | No† |
| `CADB0823` | `ExtensionCaughtSqlError` | Extension code caught an error from its own SQL and returned. The transaction can only roll back. HTTP 400. | Let the error propagate. Roll back. | No† |
| `CADB0824` | `TransactionRollbackOnly` | A statement or commit ran on a transaction that an earlier failed statement aborted. A commit rolls it back. The message names the failed statement. HTTP 409, gRPC `FAILED_PRECONDITION`. | Roll back. Stop catching the error and continuing. Handle the named error. | No† |
| `CADB0825` | `TriggerMutationConflict` | A `BEFORE` trigger's SQL changed a row that its statement was about to write. HTTP 409. | Fix the trigger. | No† |
| `CADB0826` | `TriggerQueueTooLarge` | Rows kept for `AFTER` triggers passed `spill_threshold_bytes`. HTTP 400. | Write fewer rows per statement, or raise the budget. | No† |
| `CADB0827` | `ExtensionManifestInvalid` | No `[assembly: CamusExtension]` manifest, or a bad name, version, or `Requires` entry. | Fix the manifest. | Operator† |
| `CADB0828` | `ExtensionNotAvailable` | `CREATE` or `ALTER EXTENSION` names a build this node does not load. HTTP 400. | Deploy the file and list it in `extension_assemblies`. | After fix† |
| `CADB0829` | `ExtensionAlreadyInstalled` | The extension is already installed in this database. HTTP 409. | Skip the statement. | No† |
| `CADB0830` | `ExtensionNotInstalled` | `ALTER` or `DROP EXTENSION` or `CREATE TRIGGER` needs an extension not installed here. HTTP 404. | Install it, or fix the name. | No† |
| `CADB0831` | `ExtensionRequirementMissing` | A required extension is not installed in the database. HTTP 400. | Install it first. | No† |
| `CADB0832` | `ExtensionMemberConflict` | Another installed extension defines the same member name. HTTP 409. | Drop one, or rename the member. | No† |
| `CADB0833` | `ExtensionHasDependents` | Dependent extensions or objects block a drop, or the target version lacks a member in use. The message lists them. HTTP 409. | Drop the dependents first. | No† |
| `CADB0834` | `ExtensionNotLoadedOnNode` | A statement uses an installed extension that this node does not load in that build. HTTP 503. | Deploy the same file on every node. | After fix† |
| `CADB0835` | `ExtensionClusterMismatch` | A live node does not load the target build for an install or update. The message names the nodes. HTTP 409. | Deploy the file there and restart them. Run it again. | After fix† |
| `CADB0836` | `TriggerRowMappingInvalid` | A typed trigger's row class does not fit the table. The message lists the problems. HTTP 400. | Fix the row class or the table. | No† |
