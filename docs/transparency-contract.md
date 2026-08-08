# Hermes Tech public transparency contract

This document defines how public statements about Hermes Tech stay aligned with
the executable implementation.

## Authoritative facts

| Public fact | Executable source of truth |
|---|---|
| RSS text ingestion | `collector_core.py::entry_texts()` |
| Configured source set | non-comment entries in `feeds.txt` |
| Model identifier | `digest_core.py::DEEPSEEK_MODEL` |
| Digest item count | `digest_core.py::DIGEST_ITEM_COUNT` |
| Editorial rules | `editorial/VOICE.md`, `WRITING.md`, `REVIEW.md` |
| Automatic generation behavior | `run_digests_core.sh` |
| Publication rollback/recovery behavior | `publish_core.sh` and `sync_generated_content.sh` |
| Cross-category and quality gates | `digest_core.py` and `digest_diversity.py` |
| Production time semantics | `hermes_time.py` and timezone shell adapters |
| Runtime configuration keys | `.env.example`, `digest_core.py`, `run_digests_core.sh`, and `hermes_runtime.py` |

## Required wording boundaries

### Source ingestion

Hermes Tech parses configured RSS feeds. For each entry, it stores the
feed-provided `entry.content` value when present, otherwise the RSS summary. It
strips HTML and caps stored text. It does not claim to fetch every linked
article page.

### Human supervision

Hermes Tech is human-operated and repository changes are reviewed. Humans own
policy, code, production authorization, incident handling, and optional content
review. The normal digest pipeline may automatically publish categories that
pass its executable gates; manual approval is not required for every run.

Do not use `human-supervised`, `human-reviewed before publishing`, or equivalent
wording without immediately explaining this boundary.

### Publication and recovery

Publication has two distinct recovery phases and public wording must not collapse
them into a blanket "everything rolls back on any failure" claim.

Before the publication database update is committed, failures restore the
previous generated content, Open Graph image, and live Hugo files. The database
update is committed only after the staged Hugo build and live-file deployment
have succeeded.

After that database commit, the page and database represent a successful live
publication. Git staging, commit, no-op verification, or push failures are a
**Git synchronization recovery state**, not a reason to perform a second
publication mutation. The live files and database remain in place, the command
returns the dedicated recovery exit code, and staged files or a local
publication commit are preserved where applicable so synchronization can be
completed safely.

Public copy may describe the pre-database phase as rollback/atomic recovery, but
must describe post-database Git failures as pending synchronization/recovery.
It must not imply that a successful live publication is rolled back merely
because the later Git synchronization failed.

### Models and counts

The model name and digest item count may be stated publicly only when they match
the constants in `digest_core.py`. The source count must not be hard-coded in
public prose because `feeds.txt` changes independently; say `configured RSS
feeds` instead.

### Costs

API cost varies with source volume, token usage, retries, caching, and provider
pricing. Do not state a permanent monthly amount. A dated cost report may state
what was observed for that period and must identify itself as a snapshot.

## Public surfaces

The executable checker is `tests/test_documentation_contract.py`. It covers:

- `README.md`;
- `site/content/how-hermes-works.md`;
- `site/static/llms.txt`;
- the footer in `site/layouts/_default/baseof.html`;
- `.env.example`.

Historical digests are not rewritten merely because this contract changes.

## Update procedure

When ingestion, model selection, item count, review behavior, environment keys,
or publication logic changes:

1. change executable code and tests first;
2. update every affected public surface in the same PR;
3. update this contract when the semantic boundary changes;
4. run `python -m unittest tests.test_documentation_contract -v`;
5. run the complete `bash tools/ci.sh` contract;
6. review the rendered Hugo page before any separately approved deployment.

CI must fail on stale fixed source counts, fixed monthly cost claims, incorrect
full-page ingestion claims, ambiguous per-run human-review claims, unknown
`.env.example` keys, model/item-count drift, or publication-recovery wording
that contradicts the pre-database rollback / post-database Git-recovery boundary.
