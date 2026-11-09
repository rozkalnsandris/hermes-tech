# Manual runtime/dependency rollout

Hermes Tech intentionally does **not** auto-deploy a `main` revision classified
`RUNTIME_ROLLOUT_REQUIRED`. After the SIMPLE-DEPLOY web cutover the legacy
pull-deploy timer may remain retired; the daily publication gate still fails
closed on a stale runtime and classifies the exact `production..main` range when
no exact readiness state exists. The Python/dependency transition requires an
explicit exact-SHA manual action.

The repository-owned entrypoint is:

```bash
cd /home/andris/hermes-tech-worktrees/release-control
./tools/pull-deploy/runtime-rollout.sh <exact-40-char-main-sha>
```

Use `--preflight-only` first when validating a new runtime transition:

```bash
./tools/pull-deploy/runtime-rollout.sh <exact-40-char-main-sha> --preflight-only
```

The launcher creates a private evidence directory and calls the installed,
root-owned `/usr/local/sbin/hermes-tech-runtime-rollout` through the narrow sudo
rule. The installed helper, not the target checkout, owns the privileged safety
policy.

## Fail-closed contract

Before production mutation the helper requires all of the following:

- the requested SHA is the exact current `origin/main` and production is its
  fast-forward ancestor;
- the canonical installed classifier returns exactly
  `RUNTIME_ROLLOUT_REQUIRED` and no DB-sensitive path is present;
- exact-SHA `main` CI and the single `validate` job are successful;
- poll, digest, collector and publisher locks can all be acquired with bounded
  waits;
- the production checkout contains no tracked/staged drift and only the existing
  same-date generated-digest allowlist may remain untracked;
- every pending digest is hashed before the rollout and must remain byte-identical;
- SQLite schema preflight is read-only, reports `needs_change=false`, is
  `apply_safe`, and `quick_check` is `ok`;
- an SQLite online backup passes `quick_check`;
- an isolated candidate venv installs only the hash-locked dependency files,
  passes `pip check`, the dependency contract and import smoke tests;
- the exact Hugo release is staged outside production and the generated tree is
  normalized to nginx-readable `0755` directories and `0644` files.

No `sqlite_schema.py apply` operation exists in this path. A DB-sensitive target
must use its separately authorized schema workflow.

## Apply and fail-stop

The production venv is never moved from a candidate path. The old venv is first
retained under the private runtime-backup directory, then the new venv is rebuilt
**at the final production path** so embedded interpreter/shebang paths are
correct. Git advances only with `merge --ff-only`; the prebuilt Hugo tree is then
rsynced into production and permissions are normalized again.

Post-apply checks require:

- exact production SHA and Python version;
- hash-locked dependency health and `pip check`;
- `run_collector.sh --check` and `run_digests.sh --check`;
- real `digest.py validate` whenever pending digests exist;
- unchanged live SQLite SHA256 and unchanged pending-digest SHA256 values;
- public HTTPS health.

Any failure preserves the observed production state and evidence and stops.
There is no automatic rollback, cleanup, retry, reset, or alternate mutation
path after a failed rollout. The retained old venv and temporary public-tree
backup are recovery evidence only; using them requires a new exact owner
authorization.

## Control-plane handoff

The SIMPLE-DEPLOY web cutover retires the legacy pull-deploy timer, so a runtime
rollout never enables, disables, starts, stops, or restores that timer/service.

If the runtime target also changes `tools/pull-deploy/**`, the deploy classifier,
or pull-deploy systemd units, install the exact target control-plane files first
under a separately authorized host mutation. The installer records
`installed-control-plane-sha` but does not enable the retired timer. During the
runtime rollout, an exact installed-control-plane marker equal to the target SHA
allows readiness to be recorded as `CURRENT`; otherwise the helper records
`WAIT_CONTROL_PLANE_APPROVAL` and stops short of claiming publication readiness.

## Trusted Python source boundary

The installed runtime helper contains an allowlist of Python versions and
official python.org source SHA256 values. A future Python version must therefore
be introduced in two reviewed steps:

1. extend the trusted checksum allowlist in a control-plane-only PR and activate
   that exact control plane;
2. merge the `.python-version`/dependency change and run the manual runtime
   rollout for its exact SHA.

This prevents a not-yet-approved target control-plane change from redefining the
privileged source-download trust boundary.
