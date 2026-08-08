# Hermes Tech generated-content publisher credential boundary

Hermes Tech automatically publishes generated digest content to `main`. The
normal publisher path is intentionally narrow, but the raw GitHub credential
used for that path has a wider capability than the publisher operation itself.
This document records that residual risk explicitly and defines where the
replacement must be implemented.

## Current repository-side controls

The normal generated-content path is `sync_generated_content.sh`. Before its
network push it requires, among other checks:

- the expected repository branch and remote;
- exact local/remote base equality;
- a publication commit that is a direct child of that base;
- the exact `Publish <category> digest <date>` commit subject;
- changed paths restricted to that publication's digest source, Hugo content
  file and Open Graph image;
- a second fetch immediately before push;
- a fast-forward push of the exact expected commit SHA;
- a post-push fetch proving remote `main` equals the expected commit SHA.

Repository `main` also has two intentionally separate ruleset layers:

1. `Hermes Tech main integrity` has no bypass actor and enforces deletion,
   non-fast-forward and linear-history protection;
2. `Hermes Tech code PR gate` requires the PR/status-check path for normal code
   changes but intentionally grants the production `DeployKey` an `always`
   bypass so generated-content publication can fast-forward directly.

These controls make the **normal publisher operation** fail closed. They do not
turn the raw deploy-key credential into a path-scoped capability.

## Confirmed residual risk

The host-side read-only audit recorded in issue #95 established that the single
production write deploy key is protected by strict Unix file permissions and was
not readable from the audited node-exporter container. It also established that
the key is owned by the shared `andris` UID used by multiple long-lived
processes/services.

Therefore the remaining threat boundary is same-UID arbitrary code execution:
a compromised process running as that UID is not separated by Unix mode `0600`
from another file owned by the same UID. If it obtains the raw deploy key, it is
not forced through `sync_generated_content.sh` and can attempt repository-wide
fast-forward writes permitted by that credential/ruleset bypass.

No evidence currently shows that the key is compromised. This is a
least-privilege design gap, not an incident finding.

## Why a repository path rule is not the current solution

GitHub documents deploy keys with write access as repository write credentials.
The deploy-key entry in a ruleset bypass is actor-level; it does not constrain
the actor to Hermes generated-content paths.

GitHub's file/folder path restrictions are push-ruleset functionality. Current
GitHub documentation does not provide that control as a usable path-scoped
server-side capability for this public personal-repository design. The project
must not claim that the client-side path allowlist is independently enforced by
GitHub.

This availability statement must be re-checked against current GitHub
documentation before any future architecture decision because plan/features can
change.

## Production pull-deploy boundary

The RPi5 pull-deploy control plane independently refuses to deploy an arbitrary
new `main` SHA unless exact-SHA CI is successful. Changes to the CI workflow,
pull-deploy implementation or its systemd units additionally require a local
exact-SHA control-plane approval before the installed poller will deploy them.

That is useful defense in depth for **automatic production deployment**, but it
is not a complete repository-write containment mechanism. A compromised deploy
key could modify repository code or CI before the post-push checks execute, so
post-push CI must not be described as equivalent to a pre-push server-side path
restriction.

## Required replacement owner

The host credential-isolation replacement belongs to
`rozkalnsandris/RPi5_main#93`.

The preferred design under review is:

1. move the repository write credential out of the shared application UID into
   a dedicated non-login publisher identity (or an equivalently isolated
   credential boundary);
2. expose to normal Hermes runtime only a narrow helper operation, not raw key
   readability or arbitrary Git execution as the publisher identity;
3. make that helper independently re-verify repository identity, expected
   remote/base SHA, direct parent relation, exact generated paths/subject,
   fast-forward state and post-push SHA before/after the network write;
4. prove synthetic allowed and forbidden cases before production migration;
5. document tested credential rotation/revocation and publication recovery
   before the old key is moved or revoked.

A GitHub App may be evaluated later for shorter-lived/finer-grained GitHub-side
credentials, but it does not solve the host isolation problem if its private
credential remains readable by the shared application UID.

## Change and closure gate

Hermes Tech issue #95 must remain open until the host-side replacement is
source-reviewed, test-proven and either deployed with separate production
approval or explicitly deferred with a recorded risk acceptance.

Do not rotate/revoke/move the current key, change repository rulesets, change
service users, alter SSH configuration or disable automatic publication merely
because this document is merged.

`HERMES_TECH_DEPLOY_REQUIRED=no`

`RPI5_MAIN_CHANGE_REQUIRED=yes` — implementation owner is `RPi5_main#93`.
