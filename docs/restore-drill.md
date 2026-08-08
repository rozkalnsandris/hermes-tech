# Hermes Tech end-to-end restore drill

Hermes Tech backup creation is host infrastructure owned by
`rozkalnsandris/RPi5_main`. This repository owns the **application recovery
acceptance criteria**: what must be true after a retained encrypted host backup
has been restored into an isolated location.

## Why the drill exists

Archive creation-time checks prove that an encrypted archive can be produced,
decrypted and listed. They do not prove that a retained archive can recreate a
usable Hermes Tech checkout, database and Hugo source tree later.

A restore drill closes that gap without restoring over production.

## Ownership boundary

`RPi5_main` owns:

- selecting a real retained encrypted backup;
- obtaining the matching `.sha256` sidecar and verifying the encrypted object;
- providing the age private key without copying it into evidence;
- decrypting/extracting into a new temporary restore root;
- ensuring the restore root is not `/` and is not `/home/andris/hermes-tech`;
- timing the operation from archive availability through Hermes acceptance;
- deleting the plaintext temporary restore after evidence is captured;
- scheduling/cadence and operator notification.

Hermes Tech owns `tools/verify_restore_root.py`, which starts only after the
archive is extracted. It expects the restored application at:

```text
<restore-root>/home/andris/hermes-tech
```

The verifier refuses to run if that path resolves to the live production
`/home/andris/hermes-tech` directory.

## Hermes acceptance criteria

A drill is PASS only when all of these checks succeed on the isolated restore:

1. required application paths exist, including `.git/HEAD`, `.env`, the SQLite
   database, Hugo configuration and runtime entrypoints;
2. `.env` is a regular file with no group/other permission bits; its contents
   are never read or emitted by the verifier;
3. the restored Git repository has a readable 40-character HEAD and
   `git fsck --no-dangling --no-reflogs` succeeds;
4. `data/hermes.db` opens read-only, `PRAGMA quick_check` returns `ok`, the
   database is versioned, and `articles` + `sources` exist;
5. the database file SHA-256/size are unchanged by verification;
6. Hugo can build the restored source into a separate temporary output and
   produces non-empty `index.html`, `sitemap.xml` and `robots.txt`;
7. no check writes into the restored application tree except the external
   extraction that happened before verification.

Example after an operator has extracted a retained archive:

```bash
python tools/verify_restore_root.py \
  --restore-root /var/tmp/hermes-restore-DRILL-ID \
  --evidence /var/tmp/hermes-restore-evidence-DRILL-ID.json
```

The evidence file must live outside the plaintext restored tree. It contains
paths, Git HEAD, sizes, hashes, counts and PASS status, but never `.env` values,
archive plaintext, visitor data, age keys or tokens.

## Cadence and recovery objectives

The host backup source schedules an encrypted backup every night at 02:00.
Therefore the Hermes application **RPO target is at most 24 hours** while that
nightly schedule is healthy.

Run a real retained-backup restore drill **at least once every 90 days** and
after material backup-format/encryption/restore-path changes.

The initial **RTO objective is two hours** from having the selected encrypted
archive + key available to a PASS from the Hermes acceptance verifier. Record
actual elapsed time for every drill. The two-hour value is an operational
objective, not a proven guarantee until real drills demonstrate it.

## Evidence to retain

For each drill retain a sanitized record containing:

- drill date and selected backup timestamp/name;
- encrypted backup SHA-256 (not plaintext archive contents);
- source `RPi5_main` revision and Hermes restored Git HEAD;
- verifier JSON evidence;
- start/end timestamps and measured restore duration;
- PASS/FAIL plus a short failure category if applicable;
- cleanup confirmation for the plaintext temporary restore.

Do not commit `.env`, decrypted archives, private keys, credentials, account
identifiers, visitor logs or other secret/personal data to either repository.

## Production boundary

A drill must never overwrite, rename, chmod, delete, restart, deploy or otherwise
mutate `/home/andris/hermes-tech`, its live database, `.env`, services or
Cloudflare configuration. A failed drill is recovery evidence, not permission
to modify production.

`HERMES_TECH_DEPLOY_REQUIRED=no`

`RPI5_MAIN_CHANGE_REQUIRED=yes` for the host-side retained-archive selector,
extract/cleanup operator, cadence and sanitized drill evidence lifecycle.
