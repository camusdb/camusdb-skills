# CADB08xx error codes

Read this file when you see a `CADB08xx` code. Base the code's logic on the
code, not on the message text. `CADB0837 StorageCorrupted` is also in this
range, but it is a storage fault, not an extension error. See the
`camusdb-troubleshooting` skill for it.

## DLL loading and deploy

| Code | Name | Cause | Fix |
| --- | --- | --- | --- |
| `CADB0800` | `ExtensionPathInvalid` | An extension path is relative, empty, or listed twice. | Use each absolute path once. |
| `CADB0801` | `ExtensionFileNotFound` | A configured file does not exist. | Copy the DLL to the node. |
| `CADB0802` | `ExtensionNotAnAssembly` | The file is not a loadable .NET assembly. | Fix the build output. |
| `CADB0803` | `ExtensionContractMismatch` | Built against a newer contract than the server supports. | Pin `CamusDB.Extensions.Abstractions` to the server's minor version. |
| `CADB0804` | `ExtensionDuplicateAssembly` | Two files declare the same name and version. | Remove one. |
| `CADB0805` | `ExtensionDuplicateMember` | A member collides with a built-in, or is defined twice in one extension. | Rename the member. |
| `CADB0806` | `ExtensionVerificationFailed` | A safe extension uses a disallowed API or assembly. | Remove the API, or review it and use the unsafe list. |
| `CADB0807` | `ExtensionAssemblyRefused` | A safe extension tried to load a disallowed assembly at runtime. | Same as `CADB0806`. |
| `CADB0808` | `ExtensionInvalidSignature` | A function, trigger, or procedure has a shape CamusDB cannot bind. | Fix the signature. Use `IReadOnlyList<T>`, not `T[]`. |
| `CADB0827` | `ExtensionManifestInvalid` | No manifest, or a bad name, version, or requirement. | Add a valid `[assembly: CamusExtension(...)]`. |

## Install and update

| Code | Name | Cause | Fix |
| --- | --- | --- | --- |
| `CADB0828` | `ExtensionNotAvailable` | The extension or version is not loaded on this node. | Deploy it and restart. |
| `CADB0829` | `ExtensionAlreadyInstalled` | Already installed in this database. | Use `ALTER EXTENSION ... UPDATE`. |
| `CADB0830` | `ExtensionNotInstalled` | Not installed here. Also a `CREATE TRIGGER` from a loaded but uninstalled extension. | Run `CREATE EXTENSION` first. |
| `CADB0831` | `ExtensionRequirementMissing` | A required extension is not installed. | Install it first. |
| `CADB0832` | `ExtensionMemberConflict` | Another installed extension has the same member name. | Install only one, or rename. |
| `CADB0833` | `ExtensionHasDependents` | Dependents block a drop, or the target version lacks a member in use. | Remove the listed dependents. There is no `CASCADE`. |
| `CADB0834` | `ExtensionNotLoadedOnNode` | This node does not load the installed name, version, and SHA-256. | Deploy the exact build and restart the node. |
| `CADB0835` | `ExtensionClusterMismatch` | A responding node lacks the target build for an install or update. | Deploy to the named nodes, then retry. |

## Runtime: functions, triggers, procedures

| Code | Name | Cause | Fix |
| --- | --- | --- | --- |
| `CADB0809` | `ExtensionFunctionFailed` | A scalar, table, or aggregate DLL function threw. | Fix the code or the input. |
| `CADB0810` | `ExtensionValueOutOfRange` | A `NUMERIC` does not fit `decimal`, or a returned value is out of range. | Check the value range. |
| `CADB0811` | `TriggerTypeNotFound` | No installed extension provides the CLR type. | Fix the type name or install the extension. |
| `CADB0812` | `TriggerTypeAmbiguous` | More than one installed extension provides the type. | Keep one. |
| `CADB0813` | `TriggerTypeInvalid` | The type does not implement the trigger contract. | Implement `ICamusTrigger` or `ICamusTrigger<TRow>`. |
| `CADB0814` | `TriggerAlreadyExists` | The table already has a trigger with that name. | Use another name, or drop it first. |
| `CADB0815` | `TriggerNotFound` | `DROP TRIGGER` names a missing trigger. | Check `SHOW TRIGGERS`. |
| `CADB0816` | `TriggerTargetInvalid` | The target is a view, a materialized view, or another unsupported object. | Use a plain table. |
| `CADB0817` | `TriggerBindingMismatch` | The installed extension version lacks the trigger's CLR type. | Install a version that has it. |
| `CADB0818` | `TriggerFailed` | Trigger code threw. The statement rolled back. | Fix the trigger or the data. |
| `CADB0819` | `ExtensionNestingTooDeep` | Triggers and procedures nested more than 16 levels. | Break the recursion. |
| `CADB0820` | `ExtensionStatementRefused` | Extension SQL tried DDL, transaction control, or another unsupported statement. | Use DML and `SELECT` only. |
| `CADB0821` | `ProcedureNotFound` | `CALL` names no installed procedure. | Install the extension, or fix the name. |
| `CADB0822` | `ProcedureFailed` | The procedure threw. All its changes rolled back. | Fix the code or the input. |
| `CADB0823` | `ExtensionCaughtSqlError` | Code caught a nested SQL failure. The transaction is rollback-only. | Let the error propagate. |
| `CADB0824` | `TransactionRollbackOnly` | A statement failed and aborted the transaction. Later statements are refused. `COMMIT` rolls back. | Roll back and run the work again. |
| `CADB0825` | `TriggerMutationConflict` | A `BEFORE` trigger changed a row the outer statement still writes. | Do not change that row from the trigger. |
| `CADB0826` | `TriggerQueueTooLarge` | Rows kept for `AFTER` triggers passed `spill_threshold_bytes`. | Write fewer rows per statement. |
| `CADB0836` | `TriggerRowMappingInvalid` | A typed row class does not fit its table. The message lists the problems. | Fix the property names and types. |

## Inline functions and triggers

| Code | Name | HTTP | Cause | Fix |
| --- | --- | --- | --- | --- |
| `CADB0838` | `InlineFunctionCompileFailed` | 400 | Compile error, or the compile deadline passed. Gives line and column. | Fix the body. |
| `CADB0839` | `InlineFunctionRefused` | 400 | The source or the signature is outside the allowed subset. Gives the body positions. | Remove the refused construct or type. |
| `CADB0840` | `InlineFunctionBudgetExceeded` | 400 | A call or fire passed a time, allocation, array, or stack budget. | Make the body cheaper, or raise the budget setting. |
| `CADB0841` | `InlineFunctionsDisabled` | 400 | The creation flag is off, or the build has no compiler. | Ask the operator to set `inline_functions_enabled` or `inline_triggers_enabled` on every node and restart. |
| `CADB0842` | `FunctionAlreadyExists` | 409 | The function exists, or its name collides with another function or member. | Use `CREATE OR REPLACE`, or another name. |
| `CADB0843` | `FunctionNotFound` | 404 | `DROP`, `ALTER`, or `SHOW CREATE FUNCTION` named a missing inline function. | Check `SHOW FUNCTIONS`. |
| `CADB0844` | `FunctionHasDependents` | 409 | A view, materialized view, or `CHECK` blocks a drop or a signature change. | Drop the dependents first, or change only the body. |
| `CADB0845` | `InlineFunctionUnavailableOnNode` | 503 | This node cannot compile required function or trigger code. | Upgrade the node. If a trigger body names a dropped column or function, drop and recreate the trigger. |
| `CADB0846` | `TriggerOwnerMissing` | 403 | The inline trigger's owner no longer exists. | Drop the trigger and create it again as an existing user. |
