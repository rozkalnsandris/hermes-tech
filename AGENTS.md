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

<!-- BEGIN FAST-LANE-V2.1-MANAGED -->
## FAST-LANE v2.1 Hybrid

Use `docs/FAST_LANE_V2_1.md` for the local adoption of the shared cross-project policy.

- FAST is source-only work through Ready with no live publish/deploy/restart or trust-boundary activation.
- A FAST PR may batch 2-5 closely related same-risk work items when they form one coherent acceptance story.
- After initial publication, at most two scope-preserving corrective commits may address CI/review findings; a third correction or material scope/risk expansion requires STOP.
- Use one Ready receipt, then refresh mutable state immediately before merge.
- STRICT includes publishing/deploying content, service/runtime mutation, secrets/credentials, host/root, Cloudflare and equivalent live authority.
- Merge remains explicit owner authority and never authorizes production publication/deployment.

Existing Hermes Tech editorial and production safety rules remain stricter where applicable.
<!-- END FAST-LANE-V2.1-MANAGED -->
