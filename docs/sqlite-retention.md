# Hermes Tech SQLite retention and maintenance policy

This policy covers `data/hermes.db`, which stores RSS article identity, source,
summary/content, routing and publication provenance. It intentionally separates
**measurement** from **mutation**.

## Current decision

Hermes Tech retains article rows indefinitely. The `articles.link` uniqueness
constraint is part of the collector deduplication contract, and historical rows
also preserve the article IDs referenced by generated digest metadata. Deleting
old rows automatically would weaken both properties.

There is no automatic row pruning, `content` pruning, `VACUUM`, `auto_vacuum`
change or SQLite maintenance cron job.

The large `content` field is the only field currently considered eligible for a
future bounded-retention operation. A future maintenance change may propose
clearing `content` for rows older than 90 days while retaining ID, source,
title, link, summary, fetched/published/routing metadata and all source counters.
That is a **candidate design only**, not an authorized write operation.

## Read-only measurement

Run the repository tool between collector/publisher writer windows:

```bash
python tools/sqlite_maintenance.py --db "$HOME/hermes-tech/data/hermes.db"
```

The tool opens SQLite in read-only/query-only mode and has no mutation
subcommand. It validates the current schema and `quick_check`, records the DB
SHA-256, reports main/sidecar sizes, page/freelist metrics, current
`auto_vacuum` and journal mode, row counts, recent 36-hour/7-day/30-day input,
30-day payload growth, and the amount of full `content` older than 90 days. It
fails closed if the main database file changes while the report is being built.

A maintenance review is required when any of these evidence thresholds is met:

- main DB size is at least 256 MiB;
- unused pages represent at least 32 MiB **and** at least 20% of the database
  pages;
- full article `content` older than 90 days occupies at least 128 MiB.

These are **review triggers, not automatic actions**. Below the thresholds the
policy is to allow SQLite to reuse free pages and to avoid maintenance writes
that have no measured benefit.

## VACUUM policy

Do not enable `auto_vacuum=FULL` or `auto_vacuum=INCREMENTAL` as a routine
response to growth. SQLite documents that auto-vacuum may increase
fragmentation, while a full `VACUUM` rebuilds the database and may require up to
approximately twice the database size in free disk space during the operation.
For that reason Hermes Tech performs no periodic VACUUM.

A future `VACUUM` may be proposed only after an approved deletion/content-prune
operation and only when the read-only report still shows materially reclaimable
space. The maintenance plan must include a quiet writer window, sufficient free
disk space, a verified pre-change backup, explicit rollback/evidence steps and a
post-change `quick_check`.

`VACUUM INTO` may be evaluated for an isolated compact copy, but it is not part
of the current production path and does not replace the encrypted host backup
contract.

## Production write gate

Any future pruning, deletion, VACUUM or pragma change is a separate production
operation. It must not be hidden inside collector startup or a normal deploy.
Before such a change:

1. capture the read-only maintenance report and exact DB SHA-256;
2. verify a consistent encrypted host backup/restore path;
3. define exact eligible rows/fields and invariants that must remain unchanged;
4. stop or otherwise serialize all SQLite writers;
5. create a new backup immediately before mutation;
6. bind the apply operation to the approved input SHA-256;
7. run the mutation transactionally where SQLite permits it;
8. verify schema, row/identity invariants and `PRAGMA quick_check` afterward;
9. retain machine-readable success/failure evidence.

Existing encrypted backups keep their normal host-level retention. A future
content prune does not retroactively rewrite or delete older encrypted backup
archives.

## Ownership and deployment

This policy and report tool are repository-only controls. They do not change the
production database, backup configuration, scheduler, SQLite pragmas, service
state or Cloudflare configuration.

`HERMES_TECH_DEPLOY_REQUIRED=no`

`RPI5_MAIN_CHANGE_REQUIRED=no`
