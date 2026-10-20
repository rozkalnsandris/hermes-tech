# GitHub-first / RDC fallback v1 — Hermes Tech adoption

Status: proposed **source-only** consumer adoption (effective only after exact-head owner merge).
Tracking issue: `hermes-tech#170`.
Immutable upstream: `rozkalnsandris/ops-workflows@4e89b75d7f496dfb43fd9c1eb10a995fad53fdd8` (accepted PR #159).
Machine adapter: `.github/github-first-rdc-v1.json`; CI: `.github/workflows/github-first-rdc-policy.yml`.

## Required route
For **each** work step, prefer supported connected GitHub operations for source, files, issues, branches/commits, PRs, CI/reviews and continuity. Use GitHub Actions for safe source tests when equivalent. RDC is not allowed for convenience, tool preference, latency or because a host checkout already exists.

An RDC exception requires an actual host-local action, proof of precise missing GitHub and applicable Actions capability, existing exact read/LIVE authorization and a minimum bounded operation. Record these public-safe fields: `STEP`, `REPOSITORY_AND_TARGET`, `GITHUB_CAPABILITY_CHECKED`, `PRECISE_CAPABILITY_GAP`, `GITHUB_CI_NOT_EQUIVALENT_REASON`, `MINIMAL_RDC_OPERATION`, `EXISTING_AUTHORIZATION`, `GITHUB_RETURN_EVIDENCE`. Then **GitHub -> minimal RDC -> GitHub**.

GitHub permission denial, 403, 429, rate limit, failure, timeout, unknown mutation result or missing authorization requires **STOP**. No RDC duplicate source writes, retries, rollback, cleanup or permission workaround.

## Preserved local boundaries
- Editorial constraints, digest generation and provenance are unchanged.
- Publication, production deploy, runtime/service, Cloudflare, host/root and protected data remain separately exact-authorized LIVE classes.
- Source merge is separately owner-authorized and does not authorize publishing/deployment.
- `AUTO-RUN FULL` is unsupported. `GITHUB-ONLY` is a separate explicit deferred-live mode, not this tool-priority rule.
- The existing `.github/workflows/simple-deploy.yml` deploy trigger has only `site/**`, `Dockerfile`, `deploy/**` and `.simple-deploy.json`. None of the governance files in this source change matches the deploy trigger. No live/production effect is authorized or requested.

This consumer adaptation does not mutate upstream or any other repository.
