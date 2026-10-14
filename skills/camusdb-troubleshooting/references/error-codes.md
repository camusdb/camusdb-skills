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
| `CADB0400` | `InvalidInput` | Invalid request shape: missing names, bad DDL/DML parameters, bad casts, malformed UUID, duplicate aliases, invalid transaction priority, invalid TTL options, bad `INSERT ... SELECT` column count, bad `GROUP BY`/`HAVING`/`DISTINCT`. Also `CREATE TABLE` without a primary key. HTTP 400. | Read the message. Fix the request. | No |
| `CADB0401` | `UnknownType` | A type CamusDB cannot handle in that context. | Use a supported type or cast. | No† |
| `CADB0402` | `DuplicatePrimaryKey` | Reserved. Duplicates usually report `CADB0300`. | As for `CADB0300`. | No† |
| `CADB0403` | `DuplicateColumn` | Same column name twice, or a rename to an existing column. | Rename. | No† |
| `CADB0404` | `UnknownColumn` | Column not present or not visible. | Fix the name. Check quoting. | No |
| `CADB0405` | `UnknownKey` | Planner or scanner got a key shape it cannot map. Uncommon; usually internal. | Capture and report. | Operator† |
| `CADB0406` | `SqlSyntaxError` | The parser cannot parse the text. | Fix the SQL. Use backticks for reserved-word identifiers. | No |
| `CADB0407` | `InvalidAstStmt` | Parsed, but the shape is unsupported or unusable. | Rewrite the statement. | No† |
| `CADB0408` | `SchemaLimitExceeded` | Name longer than `max_identifier_length`, or over `max_columns_per_table`, `max_indexes_per_table`, `max_tables_per_database`. | Shorten or reduce. | No |
| `CADB0409` | `InvalidAsOfSystemTime` | Malformed, future, or non-positive time; bad parameter; or used inside an explicit transaction. | Use a past time in autocommit. | No |
| `CADB0410` | `MalformedVector` | Vector `BYTES` not a whole number of 4-byte floats, or empty. | Fix the encoding. | No |
| `CADB0411` | `VectorDimensionMismatch` | Distance operands differ in dimension. | Use matching dimensions. | No |
| `CADB0412` | `InvalidVectorValue` | `NaN` or infinity, or zero vector in `cosine_distance`. | Clean the vector. | No |
| `CADB0413` | `StatementTooDeeplyNested` | Parse tree too deep. | Reduce nesting or split. | No |
| `CADB0414` | `ColumnStorageNotApplicable` | `STORAGE` on a type without variable-length values. | Use it only on `STRING`, `BYTES`/`BLOB`, `ARRAY`. | No† |
| `CADB0415` | `InvalidForeignKeyDefinition` | FK target invalid: column count or type, no PK or unique index, a view, or another database. | Fix the reference. | No† |
| `CADB0416` | `ForeignKeyCycle` | FK would create a cycle across tables. Self-reference is allowed. | Remove the cycle. | No† |
| `CADB0417` | `NumericValueOutOfRange` | `NUMERIC` outside ±99999999999999999999999999999.999999999. | Scale the value. | No† |

## CADB05xx: transactions, schema catch-up, prepared statements, auth, objects

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0501` | `TransactionAlreadyCompleted` | Commit or rollback on a transaction that is no longer active, or Kahuna returned a permanent commit failure. | Do not resend. Start a new transaction if the work is still needed. | No† |
| `CADB0502` | `TransactionConflict` | Lock conflict or conflicting concurrent write. Message samples contended keys. | Replay. Shorten transactions. Shard hot rows. | Replay |
| `CADB0503` | `SchemaCatchingUp` | Node is more than one schema version behind; it rejects reads and DML for that database. | Retry later or on another node. | Yes |
| `CADB0504` | `TransactionMustRetry` | Transient start, admission, routing, leader, transport, lock-wait, pre-write storage conflict, or unservable read range. | Replay with backoff. Check admission and leadership. | Replay |
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
| `CADB0533` | `FeatureNotSupported` | Parsed but not implemented, such as `REFRESH ... CONCURRENTLY`. HTTP 501. | Use the form named in the message. | No |
| `CADB0534` | `ConcurrentSchemaChange` | The definition changed under a derive-and-publish operation. Nothing applied. | Re-read the definition and run again. | Yes |
| `CADB0535` | `SequenceUnavailable` | No confirmed leader for a monotonic counter for `sequence_retry_budget_ms`. Nothing written. HTTP 503. | Run again once the election settles. | Yes |
| `CADB0536` | `InsufficientDiskSpace` | Free space below `min_free_disk_bytes`. Writes refused; reads and DDL still work. HTTP 507. | Free disk or lower the threshold. | After fix |
| `CADB0537` | `SnapshotPrecedesContentsGeneration` | `AS OF SYSTEM TIME` before a `TRUNCATE` cut. | Read at or after the cut, or `CREATE TABLE ... RELINK TO`. | No |
| `CADB0538` | `StatementNotAllowedInTransaction` | `TRUNCATE` inside an explicit transaction. | Commit or roll back, then run it in autocommit. | No |
| `CADB0539` | `BranchSnapshotProtectionLost` | Branch lost the snapshot protection at its fork point. HTTP 410. | Recreate the branch from the parent. | No |
| `CADB0540` | `LargeValueCorrupt` | Compressed or out-of-line value failed to decompress or checksum. | Investigate storage. | Operator† |
| `CADB0541` | `LargeValueNotResolved` | Internal read decoded an unfetched out-of-line value. | Report it. | Operator† |
| `CADB0542` | `SequenceAlreadyExists` | Name held by a sequence, table, or view. | Another name or `IF NOT EXISTS`. | No† |
| `CADB0543` | `SequenceDoesNotExist` | Sequence statement or function names a missing sequence. | Fix the name. | No† |
| `CADB0544` | `SequenceExhausted` | Next value would pass `MAXVALUE`. | Raise the limit: `ALTER SEQUENCE name MAXVALUE n` or `NO MAXVALUE`. | No† |
| `CADB0545` | `InvalidSequenceDefinition` | Non-positive increment, `CACHE` < 1, `MAXVALUE` < `MINVALUE`, or `START` out of range. | Fix the definition. | No† |
| `CADB0546` | `SequenceValueNotDefined` | `currval`/`lastval` before this transaction drew a value. | Call `nextval` first. | No† |
| `CADB0547` | `SequenceCallNotAllowedHere` | Sequence call where the draw count is unknowable, such as filters or joins. | Move the call. | No† |
| `CADB0548` | `SequenceDependencyExists` | Owned by an identity column, or a default depends on it. | Drop or change the dependent first. | No† |
| `CADB0549` | `SpillLimitExceeded` | Spill would exceed `spill_max_total_bytes` on the node. Nothing kept. HTTP 507. | Let other queries finish, raise the limit, or reduce the spill. | After fix† |
| `CADB0550` | `ReturningResultTooLarge` | `RETURNING` rows do not fit in one gRPC non-query reply. Rolled back. | Use a streaming query endpoint, or the count-only flag. | No† |
| `CADB0551` | `QueryNotFound` | `CANCEL QUERY` found no visible query with that id. | Check the id with `SHOW QUERIES`. | No† |
| `CADB0552` | `QueryNotCancellable` | Write, schema change, or still parsing. | Wait for it to finish. | No† |
| `CADB0553` | `QueryOwnerUnreachable` | Owning node did not answer before the cancel timeout. The query may still run. | Retry the cancel. | Yes† |
| `CADB0554` | `QueryCancelled` | The statement was stopped by `CANCEL QUERY`. Rows already received are real. | Rerun only if intended. | No† |

## CADB06xx: configuration

| Code | Name | Meaning | Typical fix | Retry |
| --- | --- | --- | --- | --- |
| `CADB0600` | `InvalidConfig` | Invalid or missing config file, wrong mode, bad ports or peers, invalid transaction, locking, priority, prepared-statement, statistics, TTL, spill, large-value, diagnostics, parser-cache, or regex settings, unknown keys, or unsupported `kahuna` options. Also a `SET CLUSTER SETTING` that breaks a cross-field invariant. | Read the message; it names the setting(s). Fix and restart, or choose a valid value. | No† |

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
| `CADB0710` | `PitrBootstrapFailed` | Startup only. A restored data root could not be resolved or replayed. Defined in engine source; not in the public docs. | Copy a fresh restored data root into place and start again. | Operator |
