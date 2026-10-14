# Physical backup, PITR, and WAL recovery

Sources: https://camusdb.github.io/docs/backup-and-restore,
https://camusdb.github.io/docs/camus-cli (Backups section),
https://camusdb.github.io/docs/wal-recovery

## What a physical backup is

- A base image of the storage engine plus the WAL written after it.
- It covers a whole node: every database on the node at once. There is no
  per-database or per-table backup or restore.
- There is no `BACKUP` SQL statement. It is an HTTP admin API.
- Restore is offline. It builds a fresh directory. It never touches the
  running server. There is no hot restore in place.
- Backup, list, and chain validation are online and safe under traffic.
- Artifacts are not encrypted. Protect `backup_dir` with file permissions and,
  ideally, an encrypted volume.

## Enable backups

Backups are off by default. Add this under `kahuna:` and restart the node:

```yaml
kahuna:
  backup_dir: /opt/camusdb/backups        # required; enables the API
  pitr_window_seconds: 3600               # > 0, at most 21600 (6 hours)
  base_snapshot_interval_seconds: 1800    # must not exceed pitr_window_seconds
  restore_root: /opt/camusdb/restores     # required for POST /v1/restore
  allow_unconfined_remote_restore: false  # keep false in production
```

- Until `backup_dir` is set, every endpoint returns `503 CADB0700
  BackupNotConfigured`.
- Until `restore_root` is set, restore returns `403 CADB070C
  RemoteRestoreDisabled`. Restore targets must be inside `restore_root`.
- Both roots must be owner-only (mode `0700`) and must not be symbolic links.
  Otherwise: `CADB070F BackupInsecureRoot`.
- A shorter base-image interval makes restore faster but uses more disk.

Cluster extras (same value on every node):

```yaml
kahuna:
  backup_cluster_id: prod-cluster-a
  backup_mac_key_file: /etc/camusdb/backup.key   # HMAC-SHA-256; keep outside backup_dir
```

## Access rules

- Authentication on: every endpoint needs a superuser bearer token
  (`Authorization: Bearer <token>`). Use HTTPS.
- Authentication off: the server accepts backup requests from loopback only.
  A remote caller is refused.

## The HTTP API

The API is under `/v1` on the server's HTTP port (`http_port`, default `5095`).
JSON bodies use camelCase.

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| `POST` | `/v1/backups/full` | none | Full backup now. |
| `POST` | `/v1/backups/incremental` | `{ "parentBackupId": "<guid>" }` | Incremental on top of a parent. |
| `POST` | `/v1/backups/coordinated` | none | Consistent backup across the cluster. |
| `GET` | `/v1/backups` | none | List the catalog. |
| `GET` | `/v1/backups/{id}/chain` | none | Resolve and validate the chain ending at `{id}`. |
| `POST` | `/v1/backups/gc?dryRun=<bool>` | none | Preview or run retention and orphan-file sweep. |
| `POST` | `/v1/restore` | `{ "leafBackupId", "targetDir", "targetTimeMs" }` | Offline restore into a fresh data root. |

```bash
curl -sX POST http://localhost:5095/v1/backups/full \
  -H "Authorization: Bearer $TOKEN"

curl -sX POST http://localhost:5095/v1/backups/incremental \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"parentBackupId":"<full-guid>"}'

curl -s http://localhost:5095/v1/backups -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:5095/v1/backups/<leaf-guid>/chain -H "Authorization: Bearer $TOKEN"
```

Drop the `Authorization` header when authentication is off and you call from
the same host. A failure looks like
`{ "status": "failed", "code": "CADB0701", "message": "..." }`.

## The camus-cli backup commands

`camus-cli` wraps every operation except restore. It is easier than curl.

```text
backup full                            take a full backup
backup incremental <parent-backup-id>  chain an incremental onto a backup
backup coordinated                     take a cluster-wide consistent backup
backup list                            list the node's backup catalog
backup chain <leaf-backup-id>          resolve and validate a restore chain
backup gc preview                      report what retention would reclaim
backup gc                              run retention now
```

- Works at the prompt, in `source` files, with `-e`, and with `-f`. Not in
  `--tui`. Needs no selected database.
- With authentication on, connect as a superuser (`-u` or `--token`).
- The backup API is REST only. With an explicit `Protocol=grpc` connection
  string, add `BackupEndpoint=` with the server's HTTP endpoint.
- `BackupTimeout=` in the connection string sets the backup timeout (default
  300 seconds).
- `backup coordinated` must reach the coordinator node. Pin `BackupEndpoint=`
  to it when `Endpoint=` lists several nodes.

```bash
camus-cli -e "backup list"
camus-cli -c "Endpoint=http://localhost:5096;Protocol=grpc;BackupEndpoint=http://localhost:5095" -e "backup full"
```

## Chains and restore points

- A chain is a full backup plus incrementals on top. A restore replays one
  chain.
- `backup chain <leaf>` (or `GET /v1/backups/{id}/chain`) validates it. It
  reports `minRecoverablePhysicalMs` and `maxRecoverablePhysicalMs`. Those are
  your possible restore points.
- Coverage belongs to the chain, not the clock. An archived chain stays
  restorable inside its own range.
- `targetTimeMs` is Unix epoch milliseconds. `0` means the latest point in the
  chain. A value outside coverage fails with `422 CADB0703`.
- If an incremental's parent aged past the retention floor, the server takes a
  full backup instead. The response shows `requestedKind`, `actualKind`, and
  `substitutionReason`.
- Every artifact is checked by size and SHA-256. Any damage fails closed with
  `CADB0706`.

## Restore runbook

Confirm each step with the user. A restore replaces what a server will serve.

1. **Validate the chain** with `backup chain <leaf-guid>`. Pick `targetTimeMs`
   inside the reported range.
2. **Restore into a fresh directory** inside `restore_root`. It must be new or
   empty. It must not be the live `data_dir`, its `kv` or `wal` subdirectory,
   or another job's target (`409 CADB0707`).

   ```bash
   curl -sX POST http://localhost:5095/v1/restore \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"leafBackupId":"<leaf-guid>","targetDir":"/opt/camusdb/restores/r1","targetTimeMs":1750000000000}'
   ```

   The response returns `dataRoot`, `partitionsRestored`, `entriesApplied`,
   `lastAppliedPhysicalMs`, the recoverable range, and the resolved chain.
3. **Stop the CamusDB server.**
4. **Start a server with `data_dir` set to `dataRoot`.** The restored storage is
   at `{targetDir}/kv`, with an empty `{targetDir}/wal`. No files need moving.
   Keep the same `kahuna.storage` value and revision settings as the backup.
5. **Verify** with `SHOW DATABASES`, `SHOW TABLES`, and row counts.
6. **In a cluster,** a restored node catches up by Raft after it rejoins. A
   full-cluster recovery restores every node to one coordinated point, then
   starts the cluster together.

To recover only one table or database: restore to a separate directory, start
a separate server on it, then `camus-dump` the needed objects and replay them
into production with `camus-cli`.

`kahuna.backup_restore_throttle_bytes_per_sec` limits the checkpoint copy rate.

A physical backup is bound to its storage revision. To cross revisions, restore
with the CamusDB version that took the backup, then use a logical dump.

## Clusters

- Issue `coordinated` backups on the coordinator. Other nodes answer `421
  CADB070E BackupNotCoordinator`.
- A topology change during the backup aborts it with `503 CADB070D`. Nothing is
  published. Retry after membership is stable.
- On a single embedded node, coordinated and full backups are effectively the
  same.

## Retention of backups

| Setting | Default | Meaning |
| --- | --- | --- |
| `kahuna.backup_retention_max_chains` | `0` | Keep at most N chains. `0` = no limit. |
| `kahuna.backup_retention_max_age_seconds` | `0` | Delete chains older than this. `0` = no limit. |
| `kahuna.backup_retention_max_bytes` | `0` | Cap total backup bytes. `0` = no limit. |
| `kahuna.backup_gc_interval_seconds` | `3600` | Background reaper period. `0` disables it. |

GC runs after each backup and on the timer. It deletes whole chains only and
never strands a leaf. Always preview first, and ask before a real run:

```bash
curl -sX POST 'http://localhost:5095/v1/backups/gc?dryRun=true' -H "Authorization: Bearer $TOKEN"
```

Disk planning: retained WAL is about `pitr_window_seconds` times write
throughput, plus the base images that overlap the window.

## Backup error codes

| Code | Name | HTTP | Meaning |
| --- | --- | --- | --- |
| `CADB0700` | `BackupNotConfigured` | 503 | `kahuna.backup_dir` unset. |
| `CADB0701` | `BackupChainInvalid` | 422 | No full root, gap, broken link, or cycle. |
| `CADB0702` | `BackupNeedsFullBackup` | 409 | Parent aged past the retention floor. Take a full backup. |
| `CADB0703` | `RestorePointOutOfWindow` | 422 | `targetTimeMs` outside chain coverage. |
| `CADB0704` | `RestoreFailed` | 500 | Failed during base copy or WAL replay. |
| `CADB0705` | `BackupParentMissing` | 404 | `parentBackupId` does not exist. |
| `CADB0706` | `BackupCorruptArtifact` | 422 | Artifact missing, truncated, extra, duplicated, or bad digest. |
| `CADB0707` | `RestoreTargetConflict` | 409 | Target exists or overlaps a protected path. |
| `CADB0708` | `BackupExactCheckpointUnavailable` | 409 | Backend cannot cut an exact checkpoint there. |
| `CADB0709` | `BackupUnsupportedFormat` | 422 | Legacy or unsupported manifest format. |
| `CADB070A` | `BackupRetryableLeadershipLoss` | 503 | Leader lost. Nothing durable applied. Retry. |
| `CADB070B` | `BackupCancelled` | 499 | Caller cancelled. Retryable. |
| `CADB070C` | `RemoteRestoreDisabled` | 403 | No `kahuna.restore_root`. |
| `CADB070D` | `BackupTopologyChanged` | 503 | Topology changed. Nothing published. Retry. |
| `CADB070E` | `BackupNotCoordinator` | 421 | Coordinated backup sent to a non-coordinator. |
| `CADB070F` | `BackupInsecureRoot` | 500 | Root is a symlink or group/world writable. |

Retryable: `CADB070A`, `CADB070B`, `CADB070D`. The rest need an operator
decision.

## WAL crash recovery basics

- Every committed change goes through a per-partition Raft log (Kommander),
  then into the key/value store (Kahuna). RocksDB backs both by default.
- At startup each partition replays its log before it serves work. Proposed and
  rolled-back entries are skipped. Committed entries are applied again if the
  KV store lagged.
- CamusDB loads schema metadata only after the replay reaches the KV store.
- A transaction that never committed never appears as committed. A committed
  distributed transaction finishes through its durable decision record, even
  after a leader change.
- Checkpoints let the Raft log compact, so recovery time stays bounded.
- You do nothing for crash recovery. Never delete or edit the `kv` or `wal`
  directories under `data_dir`.
- WAL replay restores the last committed state. To go back in time, or to
  rebuild a lost disk, use a backup.
