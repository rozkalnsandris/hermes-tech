# FAST-LANE v2.2 Composite — Hermes Tech

> Compatibility path: `AGENTS.md` already points to this v2.1 filename; these are the authoritative v2.2 rules.

## Core rule

**The human approves the RISK / DECISION. Automation executes the TECHNICAL STEPS.** Read-only checkpoints never create owner gates; STRICT is a live-risk classification, not a prompt-per-command workflow.

## FAST

Documentation, editorial/source changes, tests, static-site/source work and deterministic refactors may proceed from fresh GitHub state through Ready in one batch, including branch, PR, CI/review and up to two scope-preserving corrections. Batch 2-5 closely related same-risk items when coherent. Merge remains explicit.

## Human gate budget and Composite STRICT

Normal delivery has at most two owner gates: **MERGE**, then **COMPOSITE LIVE** only when publication/deploy/runtime mutation is required. Before the live gate, automation gathers all read-only evidence. One bounded live authorization binds exact SHA, target, allowed mutation categories, limits and exclusions; preflight and technical verification run inside one fail-closed one-shot.

Where artifacts/versions apply, use pinned tooling, build once, verify the exact candidate, re-check baseline/drift and deploy the exact verified artifact/version. Do not silently switch to newer `main`.

## Local STRICT boundaries

Publishing content, production deploy, service restart/reload, host/root mutation, secrets/credentials, Cloudflare changes or other live state mutation require the Composite Live authorization.

## Failure and evidence

Authorization is consumed at the first mutation. Any later error/ambiguity requires evidence preservation and STOP; no automatic retry, rollback, cleanup or alternate mutation path unless explicitly pre-authorized.

Use one Ready receipt and one final live receipt. Put the remaining owner decision only at the **end** under `ACTION REQUIRED`; when something must be entered/run, show the exact copyable instruction in a fenced `bash` block.

Merge never authorizes publish/deploy/runtime mutation.
