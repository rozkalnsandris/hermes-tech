# Hermes Tech — Project Instructions

These instructions apply only when Hermes Agent is working inside the Hermes Tech repository.

## Project purpose

Hermes Tech is a public DevOps / Platform Engineering / AI-agents portfolio project.

Its editorial principle is:

> Less hype. More understanding. The technology is not the story. The engineering behind it is.

## Safe change workflow

For substantial changes:

1. Inspect the current relevant files, active configuration, git diff, and runtime state first.
2. Show the exact current state before proposing a change.
3. Keep each change small, scoped, and reversible.
4. Prefer a concrete patch or deploy script over broad autonomous rewrites.
5. Preserve unrelated local modifications.
6. Create a backup before modifying important production files.
7. Validate syntax and the narrow behavior affected by the change.
8. Do not restart services, publish content, or deploy to production unless explicitly requested.
9. Never silently rewrite large parts of the live Hermes Tech pipeline.

When asked only to inspect, do not modify anything.

## Editorial architecture

The Hermes Agent global identity lives in:

`~/.hermes/SOUL.md`

Do not put Hermes Tech project-specific rules into the global SOUL.

Hermes Tech digest generation uses project-local editorial prompt files:

- `editorial/VOICE.md`
- `editorial/WRITING.md`
- `editorial/REVIEW.md`

These files are consumed by `digest.py` and shape DeepSeek-generated `💬 Hermes:` analysis.

They are not Hermes Agent global context files.

Root `SOUL.md`, `STYLE.md`, and `VALUES.md` are historical, non-canonical project references. They are retained for project history and must not be used as active digest prompt inputs. Update the three `editorial/` files when changing current digest behavior.

The source-of-truth and repository retention policy are documented in:

`docs/repository-hygiene.md`

## Digest quality constraints

Preserve the existing structured-output schema, retry logic, banned-phrase validation, diversity logic, and analysis validators unless a task explicitly targets them.

Current expected `💬 Hermes:` analysis limits are enforced by code and should not be duplicated as a second source of truth here.

Do not invent first-hand engineering experience, incidents, benchmarks, deployments, tests, or observations.

## Skill workflow

For Hermes Tech digest-specific procedures, use the installed skill:

`~/.hermes/skills/devops/hermes-tech-digest/SKILL.md`

Its editorial review reference is:

`~/.hermes/skills/devops/hermes-tech-digest/references/editorial-review.md`

<!-- BEGIN FAST-LANE-V2.2-MANAGED -->
## FAST-LANE v2.2 Composite

Read `docs/FAST_LANE_V2_2.md` as the active local startup contract.

**Primary rule:** the human approves the **RISK / DECISION**; automation executes the **TECHNICAL STEPS**.

- `START`, `turpini`, or equivalent continuation may carry source-only work through Ready when there is no live publish/deploy/restart or trust-boundary activation.
- FAST may batch **2-5 closely related same-risk work items** and use up to **two scope-preserving corrective commits** for CI/review findings.
- Normal delivery has at most two owner gates: explicit **MERGE**, then one bounded **COMPOSITE LIVE** only when publication/deploy/runtime mutation is required.
- Read-only validation, evidence refresh, CI/review inspection, candidate verification and reconciliation are technical steps, not owner gates.
- Composite Live must bind exact SHA, target, allowed mutation categories, practical limits, explicit exclusions and expected baseline when relevant. Where artifacts apply, pin tooling, build once and deploy the exact verified artifact/version.
- Authorization is consumed at the first authorized mutation. Any later error, ambiguity or drift requires evidence preservation and STOP; no automatic retry, rollback, cleanup or alternate mutation path unless explicitly pre-authorized.
- **STRICT** includes publishing/deploying content, service/runtime mutation, secrets/credentials, host/root, Cloudflare and equivalent live authority.
- Put any remaining owner decision visibly at the end under `ACTION REQUIRED` and provide exact copyable input when needed.
- Merge remains explicit owner authority and never authorizes production publication/deployment.

Existing Hermes Tech editorial and production safety rules remain stricter where applicable.
<!-- END FAST-LANE-V2.2-MANAGED -->

<!-- BEGIN GITHUB-ONLY-LIVE-ALL-V1-MANAGED -->
## GITHUB-ONLY / LIVE-ALL v1

Canonical shared contract: `rozkalnsandris/ops-workflows/docs/GITHUB_ONLY_LIVE_ALL.md` with machine invariants in `policy/github-only-live-all-v1.json`.

- `GITHUB-ONLY` (including `git hub only`) means fresh GitHub state, source/editorial/test work, and publication/deploy preparation up to but not including the first live publish/deploy/runtime mutation.
- Persist deferred rollout state as public-safe `[DEPLOY-QUEUE]` issues in `rozkalnsandris/ops-workflows`; chat or memory is never the queue.
- Merge remains separately explicit. Neither `GITHUB-ONLY` nor `LIVE-ALL` authorizes merge.
- A GitHub write whose deterministic side effect publishes content or changes production/runtime counts as live work and must not run under `GITHUB-ONLY`.
- Queue `READY` requires the final exact deployable SHA, exact target/entrypoint/preflight/verification/allowed mutations and no outstanding separate prerequisite owner gate.
- `LIVE-ALL` snapshots only open `READY` items present at command start, freshly revalidates exact SHA/target/baseline and may execute only ordinary predeclared publication/deploy mutations that this repository already permits inside that exact authorization envelope.
- Publication/deploy beyond the exact reviewed rollout, service/runtime mutation, secrets/credentials, host/root, Cloudflare infrastructure changes and equivalent separately gated authority remain excluded unless separately explicitly authorized.
- After any selected live mutation starts, error/ambiguity requires public-safe evidence preservation and STOP of the remaining batch; no automatic retry/rollback/cleanup/alternate mutation path unless explicitly pre-authorized.
- Existing Hermes Tech editorial and production safety rules remain authoritative and stricter where applicable.
<!-- END GITHUB-ONLY-LIVE-ALL-V1-MANAGED -->

<!-- BEGIN START-GITHUB-ONLY-V1-MANAGED -->
## START_GITHUB_ONLY_V1 deterministic bootstrap amendment

Startup contract: `rozkalnsandris/ops-workflows/docs/START_GITHUB_ONLY_V1.md`.
Repository manifest: `.github/start-github-only.json`.

- `START <repository> GITHUB-ONLY` refreshes local rules/handoff, the pinned shared policy and START contract, current default branch/governance capability, active PRs, active issues/dependencies, and relevant deploy-queue items before selecting the manifest-defined canonical lane.
- Revalidate mutable GitHub state immediately before every state-dependent write.
- The absence of an open issue alone is NOT a STOP condition. Do not invent speculative work.
- If declared tie-breakers cannot resolve equally authoritative lanes, report `AMBIGUOUS_CANONICAL_LANE` instead of choosing arbitrarily.
- Final routing is one of `READY_FOR_MERGE`, `PARKED`, `STOP_ERROR`, `NEW_SCOPE_OR_RISK`, `AMBIGUOUS_CANONICAL_LANE`, or `IDLE`.
- `PARKED` is session-only. **EXECUTOR** availability is session capability, not **READY** rollout eligibility.
- Executor unavailability alone must not change `READY` to `BLOCKED`; use `BLOCKED` only for rollout eligibility or contract failure.
- Repository-local stricter editorial, publication and production-safety rules remain authoritative.
<!-- END START-GITHUB-ONLY-V1-MANAGED -->
