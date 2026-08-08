# Hermes Tech repository hygiene and source-of-truth

This document defines which Hermes Tech files are intentional source artifacts,
which files are generated but tracked, and which paths must remain local and
untracked.

## Canonical editorial rules

The active digest pipeline loads exactly these project-local files, in this
order:

1. `editorial/VOICE.md`
2. `editorial/WRITING.md`
3. `editorial/REVIEW.md`

`digest_core.py::load_editorial_context()` is the executable source of truth.
`AGENTS.md` documents the same contract for Hermes Agent work inside this
repository.

Root `SOUL.md`, `STYLE.md`, and `VALUES.md` are retained as historical project
character references. They are explicitly non-canonical and are not loaded by
the digest pipeline. They must not be edited as a substitute for changing the
three active `editorial/` files.

The global Hermes Agent identity at `~/.hermes/SOUL.md` is outside this
repository and is a separate concern.

## Intentionally tracked generated content

The following publication artifacts remain tracked because GitHub is the
long-term content history and synchronization source:

- `digests/*.md` source digests, including the `selected_ids` metadata line;
- `site/content/**` Hugo content;
- `site/static/og/*.png` required per-digest OG cards;
- active brand, favicon, manifest, and static site assets.

Do not add ignore rules that match these paths.

## Local and transient state

The following are local runtime or build artifacts and must not be tracked:

- `data/`, `logs/`, `.env`, and `venv/`;
- runtime lock files and `.publish-work.*` directories;
- `.local-backups/`, local `backups/`, evidence JSON/archives, and Git bundles;
- Python test/type/lint caches;
- Hugo `public/`, `.hugo_build.lock`, `resources/`, and local Hugo caches;
- editor swap files and generic temporary files.

`.gitignore` contains the concrete patterns. `tools/check_repository_hygiene.py`
tests both sides of the policy: transient sentinels must be ignored, while
representative digest, content, OG, editorial, and brand paths must not be
ignored.

## Host-wide backup ownership

The encrypted Raspberry Pi host backup is infrastructure, not a Hermes Tech
application component. Its canonical reviewed source is the public
`rozkalnsandris/RPi5_main` repository at ownership-adoption commit:

```text
762174f12b72ad512600cfe2fc69bc80a530dadb
```

Repository visibility does not change the security boundary: credentials,
private host coordinates, runtime `.env` values, backup contents, keys, and
other sensitive deployment evidence must not be copied into Hermes Tech or
published merely because the infrastructure source repository is public.

The transfer was reviewed in `RPi5_main` PR #28. Machine-readable source blob
and SHA-256 bindings are stored there at:

```text
ops/backup/source-provenance.json
```

That provenance binds the pre-cleanup Hermes Tech snapshot
`194083f0d850c888d23f751aeb51e69a561a047a` and original introduction commit
`36b8223710fd2dbe90b6d69898ffc17c34285da1`.

After the transfer, Hermes Tech must not track duplicate host-wide
implementations at these paths:

- `ops/bin/rpi5-backup`;
- `ops/backup/rpi5-backup.conf.example`;
- `ops/cron.d/rpi5-backup`;
- `ops/logrotate.d/rpi5-backup`.

Hermes Tech retains only its application-specific backup expectations:

- preserve the Git checkout and its history;
- include the runtime `.env` only inside the encrypted host backup, never Git;
- create a consistent SQLite snapshot of `data/hermes.db` and validate it with
  `PRAGMA quick_check`;
- exclude `venv/`, logs, runtime locks, Python caches, the live database file
  during tree copy, and SQLite sidecars before adding the validated snapshot.

This repository cleanup does not read, compare, install, reload, execute, or
change `/usr/local/sbin/rpi5-backup`, `/etc/rpi5-backup.conf`, the cron entry,
the logrotate entry, keys, credentials, archives, retention, or remote storage.
Any future installed-file verification or deployment must start from an exact
`RPi5_main` commit and receive separate explicit production approval.

The deleted duplicates remain recoverable from normal Git history at the
pre-cleanup Hermes Tech commit shown above; no history is rewritten.

## Removed repository clutter

`site/.hugo_build.lock` was an empty transient Hugo lock and is removed from
tracking.

`site/layouts.bak-20260716/` contained four obsolete copies:

- `_default/baseof.html`
- `_default/list.html`
- `_default/single.html`
- `index.html`

The active `site/layouts/` versions are materially newer and remain the only
Hugo layout source. The deleted copies remain recoverable from Git history at
the pre-cleanup commit:

```text
8cdad1fc4885aee8362f0fdb66df446bd321b4cd
```

No history is rewritten and no external archive is needed.

## Static asset reference policy

The hygiene checker scans active Hugo templates, Markdown/HTML content, and
static manifests/configuration for local image, icon, SVG, manifest, and XML
references. Every referenced static asset must exist as a tracked
`site/static/**` path.

Versioned assets that are not currently detected as references are reported but
retained. They are not deleted during general cleanup because browser caches,
external previews, old published pages, or visual identity transitions can
still depend on them. Asset removal requires a separate reference review,
before/after Hugo output comparison, and visual verification.

Issue #8 intentionally removes no brand, favicon, manifest, or OG asset.

## Validation

Run the complete repository contract with:

```bash
python tools/check_repository_hygiene.py
bash tools/ci.sh
```

The CI path also runs the real publication simulations, rollback tests,
ShellCheck, secret scanning, and a temporary Hugo build. After validation,
tracked and untracked Git status must be clean.
