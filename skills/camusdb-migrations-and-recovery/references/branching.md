# Database branches

Source: https://camusdb.github.io/docs/database-branching,
https://camusdb.github.io/docs/databases, and
https://camusdb.github.io/docs/time-travel-reads

## What a branch is

A branch is an isolated, copy-on-write clone of a database at a point in time.
Creation copies no row and no index entry. CamusDB:

1. Creates a new database id for the branch.
2. Records the ancestors.
3. Pins the source snapshot so inherited reads stay valid.
4. Copies the schema metadata as of the fork timestamp.
5. Publishes the branch as an ordinary database name.

```sql
CREATE DATABASE feature_checkout BRANCH FROM prod;
CREATE DATABASE IF NOT EXISTS feature_checkout BRANCH FROM prod;
```

Preconditions:

- The source must exist (`CADB0010` otherwise).
- The target name must be free, unless you use `IF NOT EXISTS` (`CADB0012`).
- The source must have no schema change in flight (`CADB0400`).
- The source must have no foreign key still being added (refused).

A create on the node that runs a staged change waits for it first. The branch
then copies the element in `Public`.

## What a branch sees

Reads resolve in order: the branch overlay, then the source at the fork
timestamp, then deeper ancestors. The nearest data wins.

- A row updated in the branch shows the branch version.
- A row deleted in the branch hides the inherited row.
- An untouched row falls through to the source snapshot.
- Parent changes after the fork are invisible to the branch, and the reverse.

Schema isolation:

- DDL on the parent is invisible to the branch, and the reverse.
- `CREATE TABLE`, `ALTER TABLE`, and `CREATE INDEX` act in the branch namespace.
- `DROP TABLE` and `DROP INDEX` in a branch remove local metadata and overlay
  data only. They never delete inherited source rows.

Constraints:

- Uniqueness checks cover the branch plus inherited data. Inserting a key that
  exists in the snapshot conflicts. Delete the inherited row first.
- `INSERT ... ON CONFLICT` treats an inherited key as a conflict. An update
  writes the branch's own copy. A key the branch deleted does not conflict.
- Foreign keys are inherited. Checks stay inside the branch.
- A branch cannot reference a table in its source database.
- A transaction runs inside one database. It cannot span a branch and its
  parent.

`TRUNCATE` in a branch gives the branch a new storage id. The branch view
becomes empty. `RELINK TO` in the branch rebuilds the full merged pre-truncate
view under a new table name. Reclamation never touches ancestor keys.

A branch inherits a materialized view as published at the fork. It does not
inherit an in-flight refresh.

## Inspect branches

Both statements are server-level. They need no current database.

```sql
SHOW BRANCHES FROM prod;
SHOW ANCESTORS FROM feature_checkout;
```

`SHOW BRANCHES FROM <db>` lists every descendant, ordered by depth, then name.

| Column | Meaning |
| --- | --- |
| `database` | Descendant name. |
| `id` | Stable internal id. |
| `depth` | Distance from the source (`1` = direct branch). |
| `parent` | Immediate parent name. |
| `fork_timestamp` | HLC timestamp of the fork from its parent. |

`SHOW ANCESTORS FROM <db>` lists the chain from the immediate parent back to
the root. A root database returns an empty result.

| Column | Meaning |
| --- | --- |
| `database` | Ancestor name. |
| `id` | Stable internal id. |
| `depth` | Distance from the queried database. |
| `fork_timestamp` | HLC timestamp of the fork below this ancestor. |

## Drop branches

```sql
DROP DATABASE checkout_repro;
DROP DATABASE feature_checkout;
```

- Drop leaves first. A database with live descendants fails with `CADB0508
  DatabaseHasLiveDescendants`.
- A branch drop is immediate. `RELINK TO` cannot recover it. Confirm with the
  user before dropping a branch that holds unsaved work.
- `DROP DATABASE` waits for any staged schema change on the database. It returns
  only after its purge completes.
- A transient failure of the descendant scan fails the drop with the retryable
  `CADB0504`.
- Dropping a branch releases its retention hold on the source.
- In cluster mode, dropping a parent is fenced against concurrent branch
  creation. If the fence is indeterminate, the drop fails with a retryable
  error.

## Operator notes

- A live branch keeps the source history at its fork readable, independently
  of the age retention window. Many long-lived branches on a busy source hold
  back storage reclamation.
- `branch_snapshot_hold_lease_ms` (default `300000`, 5 minutes) sets the hold
  lease. The background renewer keeps it alive.
- If a hold is removed from the replicated registry, ancestor reads fail with
  `CADB0539 BranchSnapshotProtectionLost` (HTTP 410). Recreate the branch from
  the parent. Do not trust a partial view.
- A deep chain of branches makes reads probe more levels. Keep branches short
  and shallow.

Kahuna metrics to watch:

| Metric | Meaning |
| --- | --- |
| `kahuna.snapshot_floor.live_holds` | Live snapshot holds. Steady growth means branches are not dropped. |
| `kahuna.snapshot_floor.effective_floor_ms` | How far back the oldest hold pins history. |
| `kahuna.snapshot_floor.missing_protected_version_total` | Must stay `0`. |

## Limits

Branches do not support: merging back into the parent, transactions across two
branches, automatic compaction or rebase, re-parenting after a parent drop, or a
hard depth limit.

A `camus-dump` of a branch exports the full merged view. The reimport is an
independent database, not a branch.

## Migration rehearsal template

```sql
SHOW BRANCHES FROM prod;
CREATE DATABASE prod_migration_test BRANCH FROM prod;
USE prod_migration_test;
-- run migration.sql here, then verify:
SHOW CREATE TABLE orders;
SHOW INDEXES FROM orders;
SELECT COUNT(*) FROM orders;
-- after user confirmation:
DROP DATABASE prod_migration_test;
```

For per-developer or per-CI-run databases, use one short-lived branch each,
and drop it at the end of the run.
