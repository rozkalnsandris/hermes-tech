# FAST-LANE v2.1 Hybrid — Hermes Tech

Hermes Tech adopts the shared `rozkalnsandris/ops-workflows` FAST/STRICT delivery vocabulary.

## FAST

Documentation, editorial prompt/source changes, tests, static-site/source work and deterministic refactors are FAST when they do not publish content, restart services, deploy production, change secrets, or expand runtime authority.

FAST may batch 2-5 closely related same-risk work items and may proceed from fresh state through Ready in one source-only execution batch. At most two scope-preserving corrective commits may follow CI/review findings.

## STRICT

Publishing content, production deploy, service restart/reload, host/root changes, secrets/credentials, Cloudflare or other live state changes require separate explicit owner authorization.

## CI and evidence

The current CI is already a single coherent repository validation pipeline, so Phase 1 keeps it intact rather than adding unnecessary classification complexity. Use one complete Ready receipt and refresh mutable merge evidence only at merge time.

Merge remains an explicit owner gate and never authorizes publish/deploy/runtime mutation.
