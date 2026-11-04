# Locking reads and upserts

Read this file when you write `SELECT ... FOR UPDATE`, build a job queue, or
replace a read-then-write with `INSERT ... ON CONFLICT` or `UPSERT INTO`.

## Contents

- SELECT FOR UPDATE syntax
- What is locked
- When a row is held
- Isolation levels and locking modes
- Autocommit
- Refused shapes
- Job queue pattern
- INSERT ON CONFLICT syntax
- Arbiter index
- What happens to each proposed row
- Upsert conflicts

## SELECT FOR UPDATE syntax

```sql
SELECT ... FROM t [WHERE ...] [ORDER BY ...] [LIMIT n [OFFSET m]]
  FOR UPDATE [OF t] [NOWAIT | SKIP LOCKED];
```

- `FOR NO KEY UPDATE` is a synonym of `FOR UPDATE`.
- `OF t` must name the table in `FROM`, by name or alias.
- `FOR SHARE` and `FOR KEY SHARE` fail with `CADB0533 FeatureNotSupported`.
- The clause is allowed only at the end of a top-level `SELECT` or the query of an
  `EXPLAIN`. In a subquery, a derived table, a view body, `CREATE TABLE ... AS`, or
  `INSERT ... SELECT` it is a syntax error.
- It needs `SELECT` and `UPDATE` privileges on the table.

## What is locked

Each row that the statement returns is locked, until the transaction ends. The
lock is the exclusive row lock that an `UPDATE` takes.

- Another transaction cannot update, delete, or lock the row.
- A plain `SELECT` of the row is not blocked.
- A later `UPDATE`, `DELETE`, or `FOR UPDATE` of the row in your transaction does
  not wait.

The statement scans, filters, sorts, then locks candidate rows in sort order. It
reads each locked row again at its latest committed value and checks `WHERE` again.
Then it applies `OFFSET` and `LIMIT`.

- The returned values come from that second read, not from the scan.
- A row that a concurrent commit deleted or moved out of `WHERE` is not returned.
  The next row takes its place in the `LIMIT`. Its lock stays until the end.
- A concurrent change to a sorted column keeps the row in its old position.

## When a row is held

| Option | Result |
| --- | --- |
| None | `CADB0502 TransactionConflict`, as an `UPDATE` of the same row fails. Replay from `BEGIN`. |
| `NOWAIT` | `CADB0555 LockNotAvailable`. HTTP 409, gRPC `ABORTED`. Not in the automatic retry set. |
| `SKIP LOCKED` | The row is left out. It does not count toward `LIMIT` or `OFFSET`. |

A holder that is only finishing its commit is not a held lock. The statement waits
a short time, then continues. Any failure ends the transaction.

## Isolation levels and locking modes

| Transaction | `FOR UPDATE` | `NOWAIT` | `SKIP LOCKED` |
| --- | --- | --- | --- |
| Read Committed, pessimistic | Yes | Yes | Yes |
| Serializable, pessimistic | Yes | Yes | `CADB0533` |
| Optimistic | `CADB0400` | `CADB0400` | `CADB0400` |
| Read-only | `CADB0400` | `CADB0400` | `CADB0400` |

At Serializable, a scan takes a shared lock over the range before the row locks. A
full scan that covers a held row fails at that lock with `CADB0502`, also with
`NOWAIT`. A lookup by key goes directly to the row locks. Two workers scanning one
queue would each conflict with the other's range lock, so `SKIP LOCKED` is refused.

## Autocommit

Outside an explicit transaction, the statement runs in its own short read-write
transaction. Its locks end with the statement, and the rows are sent after that
commit. So it cannot protect a later request.

That transaction uses the request's isolation level, or `default_isolation_level`.
For `SKIP LOCKED` in autocommit, send the request at Read Committed. On HTTP, set
`"isolationLevel": "ReadCommitted"`.

## Refused shapes

These fail with `CADB0400`, because their rows are not table rows:

- `DISTINCT`, `GROUP BY`, `HAVING`, and aggregate functions.
- Joins, views, derived tables, and table functions in `FROM`.
- `UNION`, `INTERSECT`, and `EXCEPT`.
- `AS OF SYSTEM TIME`.
- A materialized view.
- A `SELECT` without `FROM`.

Plain `EXPLAIN` of a locking read takes no lock. `EXPLAIN (ANALYZE)` runs it, and
the returned rows stay locked until the transaction ends.

## Job queue pattern

Several workers each take the next free jobs. Run at Read Committed:

```sql
BEGIN;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT id FROM jobs WHERE state = 'ready' ORDER BY id LIMIT 10 FOR UPDATE SKIP LOCKED;
UPDATE jobs SET state = 'running' WHERE id IN (@id1, @id2);
COMMIT;
```

Commit before the long work runs. Locks last until the transaction ends.

## INSERT ON CONFLICT syntax

```sql
INSERT INTO t [(cols)] { VALUES ... | SELECT ... }
  ON CONFLICT [ (col, ...) | ON CONSTRAINT name ] DO NOTHING
  [RETURNING ...];

INSERT INTO t [(cols)] { VALUES ... | SELECT ... }
  ON CONFLICT ( (col, ...) | ON CONSTRAINT name )
  DO UPDATE SET c = expr [, ...] [WHERE cond]
  [RETURNING ...];

UPSERT INTO t [(cols)] { VALUES ... | SELECT ... } [RETURNING ...];
```

```sql
INSERT INTO counters (k, n) VALUES ('hits', 1)
  ON CONFLICT (k) DO UPDATE SET n = counters.n + excluded.n
  RETURNING k, n;
```

- In `SET` and `WHERE`, a bare `c` or `t.c` is the stored row. `excluded.c` is the
  proposed row.
- `UPSERT INTO t (cols)` updates every listed column that is not part of the
  primary key. An unlisted column keeps its stored value on update.
- The clause is SQL only. The typed row APIs (`/insert`, gRPC `InsertRow`) have no
  upsert form.
- `INSERT ... ON DUPLICATE KEY UPDATE`, `MERGE`, and an alias on the target are not
  supported.

## Arbiter index

| Target | Arbiter |
| --- | --- |
| `ON CONFLICT (a, b)` | Every unique index whose column set is exactly `{a, b}`, in any order |
| `ON CONFLICT ON CONSTRAINT name` | The unique index with that name. The primary key is `<table>_pkey`. |
| No target (`DO NOTHING` only) | Every unique index of the table, the primary key included |
| `UPSERT` | The primary key |

No matching unique index gives `CADB0400`. `DO UPDATE` must name a target.

## What happens to each proposed row

1. **The key is free.** The row is inserted. A duplicate on a unique index that is
   not an arbiter still gives `CADB0300`, and it ends the transaction.
2. **The key is taken, with `DO NOTHING`.** The row is skipped.
3. **The key is taken, with `DO UPDATE`.** The stored row is locked and read. If
   `WHERE` is not true, the row is skipped. Otherwise `SET` applies through the
   normal update path.

- A key with a `NULL` column never conflicts.
- `NOT NULL` and `CHECK` run on every proposed row first. A row that breaks one
  fails even when its key is taken.
- Two proposed rows that reach the same stored row under `DO UPDATE` fail with
  `CADB0400`.
- A repeated column in `SET` fails with `CADB0403`.
- `RETURNING` returns inserted and updated rows. A skipped row returns nothing. The
  row count is inserted rows plus updated rows.
- A sequence default still uses a value for a row that is skipped or updated.

## Upsert conflicts

The statement locks every arbiter key first, then reads the keys. "Insert, then
catch the duplicate" cannot work, because a duplicate-key error ends the transaction.

| Case | Result |
| --- | --- |
| Two transactions upsert the same new key | The second meets the lock. It gets `CADB0502`, or `CADB0504` at the lock-wait deadline. Its replay finds the committed row. |
| A plain `INSERT` of a key that an upsert locked | The insert waits, then gets a retryable error. |
| An upsert of a key that an uncommitted plain `INSERT` wrote | The younger upsert gets `CADB0502`. |
| A row committed after the transaction began | The lookup reads the latest committed state. The upsert updates it and never adds a second row. |

This holds in both locking modes and at both isolation levels. An autocommit upsert
is retried on these codes like any autocommit statement. `CADB0300` is not retried.

Docs:

- https://camusdb.github.io/docs/sql-queries#for-update
- https://camusdb.github.io/docs/sql-writes#on-conflict
- https://camusdb.github.io/docs/serializable-transactions
