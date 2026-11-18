# Limits and error codes for schema work

Read this file when a DDL or write statement fails, or when you size an index
or a batch. Sources: https://camusdb.github.io/docs/transaction-limits,
https://camusdb.github.io/docs/sql-indexes,
https://camusdb.github.io/docs/storage-layout,
https://camusdb.github.io/docs/data-types,
https://camusdb.github.io/docs/json,
https://camusdb.github.io/docs/csharp-extensions, and
https://camusdb.github.io/docs/error-codes.

## Limits

| Limit | Default | Setting | Error |
| --- | --- | --- | --- |
| Mutations per read-write transaction | 20,000 | `max_mutations_per_transaction` | `CADB0506` |
| Lifetime of a serializable read-write transaction | 1 hour | - | `CADB0505` |
| Columns per index, key plus `INCLUDE` | 32 | `max_index_columns` | `CADB0408` |
| `INCLUDE` payload per index entry | 4 KiB | `max_index_include_tuple_bytes` | - |
| Bare `STRING` length | 2,621,440 code points | - | `CADB0302` |
| Bare `BYTES` length | 10,485,760 bytes | - | `CADB0302` |
| Out-of-line threshold | 2048 bytes | `large_value_threshold_bytes` | - |
| Minimum compression saving | 12 percent | `large_value_compression_min_saving_percent` | - |
| Rows per `REWRITE STORAGE` batch | 200 | `large_value_rewrite_batch_rows` | - |
| Comment length | 65,535 characters | - | `CADB0511` |
| `NUMERIC` | Precision 38, scale 9 | - | `CADB0417` |
| `INT64`, `BIGINT` range | 64-bit signed | - | `CADB0420` |
| `INT`, `INTEGER` range | -2147483648 to 2147483647 | - | `CADB0420` |
| `SMALLINT` range | -32768 to 32767 | - | `CADB0420` |
| `JSONB` nesting depth | 512 levels | Fixed | `CADB0419` |
| `jsonb_agg`, `jsonb_object_agg` result | 268,435,455 bytes | - | `CADB0422` |

A setting value of `0` or below disables the index limits and the mutation
limit.

`CADB0408 SchemaLimitExceeded` also covers identifier length and counts of
columns, indexes, and tables. The settings are `max_identifier_length`,
`max_columns_per_table`, `max_indexes_per_table`, and
`max_tables_per_database`. The docs do not state their defaults. The engine
source sets 64, 512, 64, and 10,000.

## Mutation count

| Operation | Mutations |
| --- | --- |
| Insert one row, `K` secondary indexes | `2 + K` |
| Delete one row, `K` secondary indexes | `2 + K` |
| Update one row, `M` indexed columns changed | `1 + 2M` |
| Each out-of-line value on insert or delete | `+1` |
| TTL delete of one row | `1` + one per index entry |
| Each GIN index on insert or delete | `+1` per distinct leaf of the document |
| Each GIN index on update | `+1` per changed entry |

Examples at the 20,000 default:

- Two secondary indexes: 4 mutations per insert, 5,000 rows per transaction.
- No secondary index and four out-of-line values: 6 mutations per insert,
  3,333 rows per transaction.

Two updates of the same row count twice. Schema DDL and internal backfills are
exempt. `TRUNCATE` counts no mutations.

## Error codes

| Code | Name | Typical schema cause | Action |
| --- | --- | --- | --- |
| `CADB0010` | `DatabaseDoesntExist` | Table DDL before `CREATE DATABASE` | Create the database. |
| `CADB0011` | `TableDoesntExist` | Unknown table in DDL | Check the name. |
| `CADB0013` | `TableAlreadyExists` | `CREATE TABLE` or rename to an existing name | Use `IF NOT EXISTS` or another name. |
| `CADB0018` | `DatabaseNameReserved` | `_system` or `information_schema` | Rename. |
| `CADB0300` | `DuplicateUniqueKeyValue` | Duplicate key, or unique index backfill finds duplicates | Clean the data first. |
| `CADB0301` | `NotNullViolation` | `NULL` into `NOT NULL`; `SET NOT NULL` or `ADD COLUMN ... NOT NULL` over rows without a value | Add a `DEFAULT`, or fill the column first. |
| `CADB0302` | `ValueTooLong` | Value longer than `STRING(N)` or `BYTES(N)` | Raise the bound or shorten the value. CamusDB never truncates. |
| `CADB0303` | `CheckConstraintViolation` | Row or existing data violates a `CHECK`; an existing `CHECK` hits a type error at evaluation | Fix the data or the rule. Write a `CAST`. |
| `CADB0304` | `ForeignKeyViolation` | Child has no parent; `ADD CONSTRAINT` finds an orphan | Insert the parent first. |
| `CADB0305` | `ForeignKeyRestrictDelete` | Delete of a referenced parent | Delete the children first. |
| `CADB0306` | `ForeignKeyRestrictUpdate` | Update of a referenced parent key | Update the children first, or keep keys immutable. |
| `CADB0400` | `InvalidInput` | No primary key; `DESC` on `STRING`/`BYTES`; invalid `CHECK`, including a non-boolean condition or a wrong argument count; invalid TTL setting; bad default; bare `DROP INDEX name`; `JSONB` as an ordered key; wrong GIN index shape; comparison of types with no rule; a lone UTF-16 surrogate half in a string parameter or row write; a constant division by zero; interval overflow | Fix the DDL or the statement. |
| `CADB0403` | `DuplicateColumn` | Same column twice, or rename to an existing column | Rename. |
| `CADB0404` | `UnknownColumn` | Reference to a missing column | Check the name. |
| `CADB0406` | `SqlSyntaxError` | Syntax from another database; an unknown character, a non-breaking space, or a byte-order mark outside a literal (the message gives line and column) | See [ddl-syntax.md](ddl-syntax.md). Check copied SQL for invisible characters. |
| `CADB0407` | `InvalidAstStmt` | Unknown function, for example in a new `CHECK` | Check the function name. |
| `CADB0408` | `SchemaLimitExceeded` | Index over 32 columns; name or count limit | Reduce the schema object. |
| `CADB0414` | `ColumnStorageNotApplicable` | `STORAGE` on a fixed-width type | Remove the clause. |
| `CADB0415` | `InvalidForeignKeyDefinition` | Bad target, type mismatch, view, other database | Reference a primary key or unique index. |
| `CADB0416` | `ForeignKeyCycle` | Cycle across two or more tables | Remove one reference. |
| `CADB0417` | `NumericValueOutOfRange` | `NUMERIC` overflow | Check the value range. |
| `CADB0418` | `InvalidJsonText` | Text written to `JSONB` is not valid JSON | Fix the text. Use `STRING` if it is not always JSON. |
| `CADB0419` | `JsonNestingTooDeep` | Document nested deeper than 512 levels | Flatten the document. |
| `CADB0420` | `IntegerValueOutOfRange` | `INT64` overflow; a value outside a `SMALLINT` or `INT` column range; a sequence value too large for a narrow column; a cast of `NaN`, an infinity, or an out-of-range value to an integer | Use `BIGINT` or `INT64` for the column, or `NUMERIC` for a wider exact value. |
| `CADB0421` | `FloatValueOutOfRange` | Finite float overflow or underflow | Check the value range. |
| `CADB0422` | `JsonbValueTooLarge` | `jsonb_agg` or `jsonb_object_agg` result too large | Reduce the group. |
| `CADB0502` | `TransactionConflict` | Hot row, foreign-key lock, storage rewrite | Retry from `BEGIN`. Remove the hot row. |
| `CADB0504` | `TransactionMustRetry` | Transient routing or range move | Retry from `BEGIN`. |
| `CADB0505` | `TransactionLifetimeExceeded` | Transaction open over 1 hour | Roll back and retry from `BEGIN`. |
| `CADB0506` | `TransactionMutationLimitExceeded` | Batch too large for the index count | Do not retry. Split the batch. |
| `CADB0511` | `CommentTooLong` | Comment over 65,535 characters | Shorten it. |
| `CADB0530` | `DependentObjectsExist` | Drop or truncate of an object a foreign key or view needs; drop or rename of a column a trigger names | Drop the dependent object first. |
| `CADB0533` | `FeatureNotSupported` | `CASCADE`, `SET NULL`, `DEFERRABLE`, `MATCH FULL`, TTL parent | Use a supported form. |
| `CADB0542` | `SequenceAlreadyExists` | Sequence name taken by a sequence, table, or view | Rename. |
| `CADB0545` | `InvalidSequenceDefinition` | Non-positive increment, `CACHE` below 1, `MAXVALUE` below `MINVALUE` | Fix the options. |
| `CADB0547` | `SequenceCallNotAllowedHere` | `nextval` outside `INSERT`, defaults, or a top-level `SELECT` without `FROM` | Move the call. |
| `CADB0548` | `SequenceDependencyExists` | Drop of an owned or used sequence | Drop the column or the default first. |
| `CADB0559` | `JsonbValueCorrupt` | A stored `JSONB` cell does not decode | Damaged data, or a value from a newer build. Check the server version. |
| `CADB0600` | `InvalidConfig` | A node set to the other routing mode refuses to start | Keep the recorded mode. Dump and reimport into a new cluster to move. |
