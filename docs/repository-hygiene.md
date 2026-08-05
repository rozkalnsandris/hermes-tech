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
