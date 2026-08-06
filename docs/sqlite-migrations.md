# Hermes Tech SQLite schema migrations

Hermes Tech uses `PRAGMA user_version` as the authoritative schema version.
The current version is **3**.

| Version | Contract |
|---|---|
| 1 | `articles`, `sources`, `articles.link` uniqueness, fetched index |
| 2 | category/content columns and category index |
| 3 | routing columns and primary-category/topic-key indexes |

The migrator accepts only these exact historical shapes. An unknown partial
schema, missing uniqueness constraint, wrong column definition, missing index,
newer version, failed integrity check, lock, or read-only database is an error.
No `sqlite3.OperationalError` is suppressed.

## Runtime behavior

A brand-new empty database may be initialized automatically. An existing
unversioned or old database is **never upgraded by collector startup**. The
collector exits non-zero and points to the preflight/apply workflow below.
This prevents a cron run from becoming an unreviewed production migration.

All supported steps, including legacy version adoption, execute inside one
`BEGIN IMMEDIATE` transaction. Existing article values, digest assignments,
source rows, and source counters are snapshotted and verified unchanged before
commit. The final schema, required indexes, uniqueness constraints, and
`PRAGMA quick_check` must pass.

## Read-only preflight

Preflight opens the database with SQLite `mode=ro` and `query_only=ON`. It
records the DB SHA-256 before and after inspection and fails if the file
changes.

```bash
cd /home/andris/hermes-tech
python3 tools/sqlite_schema.py preflight \
  --db data/hermes.db \
  --evidence /path/outside/repo/hermes-db-preflight.json
```

Review at least:

- `sha256` and `read_only_unchanged`;
- `user_version`, inferred legacy version, and ordered `steps`;
- article/source row and counter totals;
- columns, indexes, uniqueness assertions, and `quick_check`;
- `sidecars`, `journal_mode`, and `apply_safe`.

Preflight is not approval to apply. Keep its evidence outside the Git worktree
and do not commit production data or evidence containing host paths.

## Separately approved production apply

Apply requires a fresh, exact DB SHA-256 from the approved preflight, a backup
directory, and a new evidence path. Before apply, stop every SQLite writer
(collector cron, digest classify/publish activity, and manual jobs). Verify no
process still has the DB open for writing.

Apply is blocked when SQLite sidecar files (`-journal`, `-wal`, `-shm`) exist or
when the database is in WAL mode. Resolve those states using normal SQLite
shutdown/checkpoint procedures; never delete active sidecars blindly.

```bash
cd /home/andris/hermes-tech
python3 tools/sqlite_schema.py apply \
  --db data/hermes.db \
  --expected-sha256 '<64-hex SHA from approved preflight>' \
  --backup-dir /path/outside/repo/hermes-db-backups \
  --evidence /path/outside/repo/hermes-db-apply.json
```

The tool obtains an SQLite write reservation before rechecking the SHA and
creating the backup. It verifies the backup hash and `quick_check` before any
schema statement runs. A successful evidence file contains the before report,
ordered applied steps, backup path/hash, and post-apply report.

A code merge or deployment never authorizes this apply command. Production
application requires its own explicit approval against the exact preflight
SHA.

## Failure and recovery

A migration error rolls back the complete chain. The tool keeps the verified
pre-migration backup and writes failure evidence when possible. Do not retry
until the failure evidence and current DB state have been reviewed.

To recover from a post-commit operational problem:

1. Stop all DB writers and preserve the problematic DB under a timestamped
   name.
2. Verify the backup path and SHA-256 against the apply evidence.
3. Copy the backup to a temporary file on the same filesystem, restore the
   intended owner/mode, then atomically rename it to `data/hermes.db`.
4. Run read-only preflight on the restored DB and require `quick_check: [ok]`.
5. Restart one writer at a time and inspect collector/digest logs.

Do not restore by editing tables manually, resetting `user_version`, deleting
sidecars while SQLite is active, or copying a database while writers are
running.

## Development tests

Text SQL fixtures cover v1, v2, and unversioned-v3 databases. Tests verify:

- one-time upgrade and repeated no-op behavior;
- exact row/counter preservation;
- empty DB initialization;
- fail-closed partial schemas and missing constraints;
- propagated malformed-SQL, read-only, and lock errors;
- byte-identical read-only preflight;
- SHA-bound backup/evidence apply and failure rollback.
