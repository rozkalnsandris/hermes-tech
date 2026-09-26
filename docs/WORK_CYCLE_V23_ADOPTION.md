# Work Cycle v2.3 adoption — Hermes Tech

Hermes Tech consumes the shared FAST-LANE v2.3 work-cycle contract from `rozkalnsandris/ops-workflows` at canonical revision `274d58f2d9d3cb86feded2751b8f9009a4501f6b` under rollout tracker `#124`.

## Routing

- `AGENTS.md` remains the primary repository-local rule source and wins whenever it is stricter.
- `.github/agent-bootstrap.json` is stable routing metadata only; it must not contain mutable SHA/PR/CI/review/runtime/authorization truth.
- Normal `START hermes-tech`, `SYNC hermes-tech`, and `turpini` retain minimum-sufficient retrieval and one selected current lane.
- Safe source/docs/tests/policy work may continue automatically through Draft PR, exact-head CI/review convergence, and Ready when no owner gate is crossed.
- Terminal responses remain compact and end with exactly one actionable command.

## Write preflight

`WRITE_PREFLIGHT_COMPACT_V1` is consumed through the existing `.github/github-api-access-v1.json` adapter. Hermes Tech does not create a second write-preflight implementation.

- exact already-satisfied branch/PR intent: reconcile or no-op;
- conflicting identity or stale writer: STOP;
- mutation authority is consumed on dispatch;
- ambiguous mutation outcome permits only minimum read-only reconciliation before STOP.

## AUTO-RUN FULL

Repository-local AUTO-RUN FULL is **not supported** in Hermes Tech. This rollout records `NOT_APPLICABLE`; it does not create a controller or rewrite historical state.

## Deployment profile

Hermes Tech already uses the shared SIMPLE-DEPLOY workflow for normal application/content changes. The bootstrap profile is therefore `simple-deploy`.

This governance rollout does not grant production/LIVE authority and must not itself deploy. The repository SIMPLE-DEPLOY trigger ignores `.github/**`, `docs/**`, and `tests/**`-only pushes so work-cycle governance merges do not create a production deployment. Normal deployable source/content changes continue to trigger SIMPLE-DEPLOY.

## Preserved stricter rules

The following remain unchanged and authoritative:

- merge requires an explicit owner decision;
- publish/deploy/runtime actions require exact LIVE authority;
- editorial and production-safety rules remain in force;
- secrets/credentials, host/root, Cloudflare, service/runtime, permissions, retry, rollback, and cleanup remain separately gated;
- merge never implies LIVE/deploy authority;
- Queue vNext is not activated by this adoption.
