<p align="center">
  <img src="assets/branding/project-logo.svg" alt="Hermes Tech winged H logo" width="128" height="128">
</p>

<h1 align="center">Hermes Tech</h1>

<p align="center">
  <strong>A self-hosted, openly AI-generated technology digest for DevOps, AI, and agent engineering.</strong>
</p>

<p align="center">
  <a href="https://tech.rozkalns.net/">Live site</a>
  ·
  <a href="https://tech.rozkalns.net/how-hermes-works/">How Hermes Works</a>
  ·
  <a href="docs/transparency-contract.md">Transparency</a>
</p>

<p align="center">
  <a href="https://github.com/rozkalnsandris/hermes-tech/actions/workflows/ci.yml">
    <img src="https://github.com/rozkalnsandris/hermes-tech/actions/workflows/ci.yml/badge.svg?branch=main" alt="Hermes Tech CI status">
  </a>
</p>

> **The technology is not the story. The engineering behind it is.**

Hermes Tech turns configured RSS feeds into a transparent daily technology
publication. A Python pipeline stores feed-provided text in SQLite, clusters
related reporting into canonical topics, uses AI to classify, generate, and
validate three category digests, then publishes a static Hugo site with RSS and
Open Graph cards.

| | |
|---|---|
| **Coverage** | DevOps · AI · AI agents |
| **Stack** | Python · SQLite · Hugo · Bash · GitHub Actions |
| **Publishing** | Static site · RSS · Open Graph cards |
| **Principles** | openly AI-generated · source-bounded · fail-closed · self-hosted |

The project is operated by Andris Rožkalns. Repository changes are reviewed
through GitHub; a successful production digest may publish automatically after
all executable gates pass and does not require a separate human approval for
every run.

## Architecture

```text
feeds.txt
   │
   ▼
collector.py ── RSS entry.content or summary fallback ──► SQLite
                                                        │
                                                        ▼
run_digests.sh ── classify ── generate ── validate ── publish
                                                        │
                                                        ▼
                                  digests/ + Hugo content + OG cards
                                                        │
                                                        ▼
                                         Git synchronization + Hugo site
```

Important boundaries:

- the collector parses RSS data; it does not fetch every linked article page;
- `digest_core.py` is authoritative for the configured model, digest item
  count, prompts, quality checks, and routing logic;
- `feeds.txt` is authoritative for the configured source set, so public text
  must not hard-code a source count;
- `run_digests_core.sh` is authoritative for automatic generation,
  validation, partial-failure handling, and publication behavior;
- API cost is usage-dependent and must not be presented as a permanent fixed
  amount. Published cost reports are point-in-time observations.

See [`docs/transparency-contract.md`](docs/transparency-contract.md) for the
public-fact update policy.

## Repository layout

| Path | Purpose |
|---|---|
| `collector.py`, `collector_core.py` | RSS ingestion and SQLite persistence |
| `digest.py`, `digest_core.py` | routing, generation, validation, and publish dispatch |
| `digest_diversity.py` | deterministic topic/vendor diversity contracts |
| `hermes_db.py`, `tools/sqlite_schema.py` | versioned SQLite schema and approved migration tooling |
| `hermes_time.py` | UTC storage and Europe/Berlin business-time contracts |
| `run_digests.sh`, `publish.sh` | public shell entrypoints with safety adapters |
| `editorial/` | canonical voice, writing, and review rules |
| `feeds.txt` | configured RSS sources and lanes |
| `digests/` | tracked source digests and selected article IDs |
| `site/` | Hugo configuration, content, layouts, and static assets |
| `tests/`, `tools/ci.sh` | executable repository contracts |
| `docs/` | migration, hygiene, ownership, and transparency documentation |

Root `SOUL.md`, `STYLE.md`, and `VALUES.md` are historical references. The
active editorial source of truth is `editorial/VOICE.md`,
`editorial/WRITING.md`, and `editorial/REVIEW.md`.

## Supported toolchain

- Python **3.11.9** from `.python-version`;
- exact Python packages from `requirements-bootstrap.txt`, `requirements.txt`,
  and `requirements-dev.txt`;
- Hugo Extended **0.164.0**, installed and checksum-verified by
  `tools/install_hugo.sh`;
- Bash, Git, rsync, ShellCheck, and standard Unix tools.

## Reproduce read-only validation

These commands require no production secrets, RSS requests, DeepSeek calls,
Telegram calls, SSH, Docker, deployment, or production database access. They
keep the repository worktree clean by installing tools below `/tmp`.

```bash
git clone <repository-url> hermes-tech
cd hermes-tech

python3.11 -m venv /tmp/hermes-tech-venv
source /tmp/hermes-tech-venv/bin/activate
python -m pip install --disable-pip-version-check \
  --requirement requirements-bootstrap.txt
python -m pip install --disable-pip-version-check --no-build-isolation \
  --requirement requirements-dev.txt

bash tools/install_hugo.sh /tmp/hermes-tech-hugo/bin
export PATH="/tmp/hermes-tech-hugo/bin:$PATH"
export HERMES_TECH_ROOT="$PWD"

bash tools/ci.sh
```

`tools/ci.sh` validates dependency pins, repository hygiene, Python
syntax/imports, all Python and shell tests, ShellCheck, secret scanning, and a
Hugo build to a temporary destination. It fails if validation changes or leaves
files in the worktree.

## Runtime configuration

Copy `.env.example` to `.env` only inside the intended runtime checkout and
supply real values there. `.env` is ignored by Git and must never be committed.

| Setting | Required | Used by |
|---|---:|---|
| `DEEPSEEK_API_KEY` | classify/generate/validate runs | `digest_core.py` |
| `TELEGRAM_BOT_TOKEN` | optional | digest and pipeline notifications |
| `TELEGRAM_CHAT_ID` | optional | digest and pipeline notifications |
| `HEALTHCHECK_URL` | optional | pipeline start/success/failure pings |

`HERMES_TECH_ROOT` is a process environment variable, not a `.env` setting. It
selects an absolute runtime root and defaults to `~/hermes-tech`. Tests and
isolated worktrees set it explicitly. Internal test-only adapter variables are
not operator configuration.

The example file intentionally contains empty values. A clean validation run
does not need `.env` at all.

## Runtime and scheduling map

| Entrypoint | Runtime responsibility |
|---|---|
| `collector.py` | collect configured RSS feeds into the current SQLite schema |
| `run_digests.sh --check` | non-publishing runtime preflight |
| `run_digests.sh` | classify, generate, globally validate, and publish passing categories |
| `publish.sh <category> <YYYY-MM-DD>` | atomic per-category publication and Git synchronization |
| `tools/sqlite_schema.py preflight` | read-only schema inspection and exact apply plan |
| `tools/sqlite_schema.py apply` | separately approved, SHA-bound schema migration |

Host cron ownership and installed-file deployment are infrastructure concerns,
not application-PR side effects. The active schedule must be verified in the
host infrastructure source of truth before changing it. A merge never
implicitly authorizes production deployment, database migration, cron changes,
or service reloads.

## Shared public ingress ownership

`tech.rozkalns.net` is intentionally public, but this repository does not own
the shared Cloudflare connector. `RPi5_main` owns the RPi5 host-level systemd
`cloudflared.service` and its credential; Cloudflare remotely manages the
published route. Hermes Tech owns only its application origin and public health
contract.

Hermes Tech deployment, publication and rollback must never install, restart,
replace, reconcile or roll back the shared connector, and no shared Tunnel
credential belongs in this repository or its runtime. Connector lifecycle and
host firewall policy are infrastructure work, separate from content
publication.

## Generated-content Git policy

Published output intentionally remains tracked:

- `digests/*.md` source digests and `selected_ids` metadata;
- `site/content/**` Hugo content;
- `site/static/og/*.png` digest OG cards.

Publication accepts only the generated-content allowlist, rejects unrelated
tracked or untracked changes, rejects local-ahead or concurrent-remote states,
uses no force-push, and verifies the exact pushed commit SHA. The executable
contracts are `tests/test_generated_content_sync.sh`,
`tests/test_publish_generated_content_integration.sh`, and
`tests/test_publish_rollback.sh`.

## Database, backup, and recovery

- SQLite schema behavior and the separately approved production migration
  process are documented in [`docs/sqlite-migrations.md`](docs/sqlite-migrations.md).
- Repository retention and generated-file policy are documented in
  [`docs/repository-hygiene.md`](docs/repository-hygiene.md).
- Host-wide encrypted backup implementation is owned by the private
  `rozkalnsandris/RPi5_main` infrastructure repository. Hermes Tech retains
  only its application-specific backup expectations and provenance references
  in the repository-hygiene document.

Never commit `.env`, SQLite databases, logs, backup archives, evidence bundles,
private host paths containing credentials, or production data.

## Contribution workflow

Use the same controlled path for every change:

1. define or select a GitHub issue with acceptance criteria;
2. create an isolated branch/worktree from the exact current `main` SHA;
3. change only the issue scope and add regression coverage;
4. open a **Draft PR**;
5. require a clean GitHub Actions run and audit the exact head SHA, diff,
   comments, reviews, and branch distance;
6. mark the PR ready only when the audit is clean;
7. perform an explicitly authorized **squash merge**;
8. treat production deployment or database apply as a separate authorization.

Do not modify the primary production checkout for development work, stack work
on an unmerged branch, force-push generated history, or interpret a merge as a
deploy instruction.

## Public transparency

The public explanation lives at `site/content/how-hermes-works.md`, with a
machine-readable summary at `site/static/llms.txt`. Their claims are checked
against the code by `tests/test_documentation_contract.py`.

Hermes Tech is always labeled as AI. Human supervision means humans own the
system, policies, code review, incident handling, and optional content review;
it does **not** mean every automatically passing digest receives manual approval
before publication.
