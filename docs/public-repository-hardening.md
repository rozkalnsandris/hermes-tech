# Public repository hardening

Hermes Tech is intended to become public so GitHub Free can enforce the prepared
`main` rulesets. Public visibility is a separate security transition, not a
normal code deployment.

## Read-only audit baseline

The 2026-08-07 V1/V2 public-readiness audits on
`1bb3e22aab2b0981eab9b2923104f7113975d3e9` established:

- no credential patterns in current tracked text;
- no credential patterns across reachable Git history;
- no credential patterns in the inspected GitHub Actions logs or artifacts;
- historical infrastructure paths and an old host-backup implementation exist
  in Git history, but no secret values were detected;
- a historical personal commit-email identity is reachable from `main`.

The owner explicitly chose not to rewrite Git history solely to replace the
historical commit email. Rewriting would invalidate commit SHAs, provenance,
production ancestry, and existing audit evidence. Before public visibility,
future local publisher/operator commits must use the GitHub noreply identity.

## Deployment boundary

A public repository must not retain the persistent repository-level RPi5
self-hosted Actions runner. GitHub-hosted CI remains the only PR execution
surface.

Production deployment is pull-based:

1. the unprivileged `andris` systemd service fetches `origin/main`;
2. it refuses anything that is not a fast-forward descendant of production;
3. it requires a successful GitHub Actions `CI` run triggered by `push` for the
   exact current `main` SHA;
4. it independently requires exactly one successful `validate` job in that run;
5. control-plane changes require an exact-SHA local approval written only by the
   activation procedure;
6. the poller invokes one narrow root helper through sudo;
7. the root helper serializes with `.publish.lock`, performs a staged Hugo build,
   fast-forwards only, verifies the public site, and rolls back both checkout and
   public files on failure;
8. dependency- and database-sensitive changes remain blocked from automatic
   deployment; database migrations are never executed by this path.

The timer polls every two minutes. A missing or failed CI produces a no-op and is
retried by the next timer run.

## Control-plane changes

Changes under any of these paths do not auto-deploy without an exact-SHA
activation:

- `.github/workflows/**`;
- `tools/pull-deploy/**`;
- `ops/systemd/hermes-tech-pull-deploy.service`;
- `ops/systemd/hermes-tech-pull-deploy.timer`.

This prevents a newly merged control-plane change from replacing the installed
poller before the old installed poller has reviewed the diff.

## Public transition order

The transition must remain fail-closed:

1. merge the reviewed pull-deploy implementation with GitHub-hosted CI passing;
2. install and activate the exact merged SHA on RPi5;
3. verify production reached that SHA and the public site is healthy;
4. deregister and remove the Hermes Tech repository self-hosted runner;
5. verify no repository-level Hermes Tech runner remains;
6. restrict repository Actions to GitHub-owned actions and require full-SHA
   pinning;
7. re-run the public-readiness audit;
8. change repository visibility to public;
9. immediately set fork workflow approval to `all_external_contributors`;
10. apply and independently verify the prepared `main` rulesets from issue #2.

Visibility, runner removal, installed-file changes, and ruleset activation remain
separately verified operational actions. A code merge alone authorizes none of
them.
