# Hermes Tech generated-content publisher credential boundary

Hermes Tech automatically publishes generated digest content to `main`. The
normal publisher operation is deliberately narrow, but the raw credential used
by that operation currently has a wider trust boundary than the operation
itself. This document records the reviewed residual risk and the replacement
contract.

## Current repository-side controls

The normal generated-content path is `sync_generated_content.sh`. Before its
network push it requires, among other checks:

- the production checkout to be on the expected branch;
- exact local/remote base equality;
- the publication commit to be a direct child of that base;
- the exact `Publish <category> digest <date>` subject;
- every changed path to belong to the exact digest/content/OG allowlist for that
  publication;
- a fresh remote read immediately before the write;
- a non-forced exact refspec push to `refs/heads/main`;
- a post-push remote read proving `main` equals the expected commit SHA.

Git itself refuses a normal branch push that is not a fast-forward. Hermes also
checks the exact expected remote/base SHA immediately before the push, so the
normal publication path is fail closed if another writer advances `main`.

Repository policy adds two separate layers:

1. `Hermes Tech main integrity` has no bypass actor and protects deletion,
   non-fast-forward history and linear history;
2. `Hermes Tech code PR gate` requires the PR/status-check path for normal code
   changes but intentionally grants the production `DeployKey` an `always`
   bypass so generated-content publication can fast-forward directly.

R1-R5 add a separate production-readiness boundary: exact deploy-impact
classification, blocked-state notification, audited runtime rollout, a
publication readiness gate and a bounded-age watchdog. These controls protect
production reconciliation and scheduled publication, but they do not turn the
raw repository credential into a generated-path-only capability.

## Confirmed host-side residual risk

The sanitized RPi5 audit completed under `rozkalnsandris/RPi5_main#111` and
confirmed:

- exactly one write-enabled Hermes Tech deploy key exists;
- exactly one local private key matches it;
- the private key is owned by the shared `andris` UID, is mode `0600`, has link
  count 1, is not a symlink, and its SSH parent directory is mode `0700`;
- the key is therefore readable by processes executing as that same UID;
- multiple live processes and long-lived services share that UID;
- the audited `hermes-dashboard.service`, `hermes-gateway.service` and
  `balkons-bot.service` currently do not enable `ProtectHome`, `ProtectSystem`,
  `NoNewPrivileges`, `PrivateTmp` or `PrivateDevices`;
- node-exporter structurally sees the host root through a read-only mount, but
  runs as `nobody`, non-privileged, under `docker-default`, and the prior direct
  readability check found the key not readable from that container;
- the normal publisher currently performs its Git push from the shared
  application boundary.

No evidence shows that the deploy key is compromised. This is a
**least-privilege design gap, not an incident finding**.

The important threat boundary is same-UID arbitrary code execution: a
compromised process running as `andris` is not separated by Unix mode `0600`
from another `andris` file. If such a process obtains the raw deploy key, it is
not forced through `sync_generated_content.sh` and can attempt repository writes
allowed to that credential and its ruleset bypass.

Sensitive host details such as the exact private-key path, SSH command, sudo
rules and local evidence files are intentionally not stored in this repository.

## GitHub credential capabilities

GitHub documents a write-enabled deploy key as a credential for a single
repository; when `read_only` is false it can write to that repository. A ruleset
bypass granted to a deploy-key actor is an actor-level exception, not a
capability that limits the actor to Hermes generated-content paths. The project
must therefore not describe the client-side path allowlist as an independently
enforced GitHub path boundary.

GitHub currently recommends GitHub Apps when finer-grained repository
permissions are needed. An installation access token can be restricted to
selected repositories and permissions and expires after one hour. That is a
useful future credential improvement, but it does not solve the RPi5 host
isolation problem if the App private key or token-minting capability remains
readable by the shared application UID.

The current RPi5 automation program already has a separate least-privilege
GitHub App read-only contract/canary. Publisher write migration must remain
coordinated with that program rather than silently broadening App permissions.

## Required replacement boundary

Host-side implementation is owned by `rozkalnsandris/RPi5_main#93` and child
`rozkalnsandris/RPi5_main#110`. The replacement must:

1. move the raw repository write credential or token-minting secret out of the
   shared `andris` UID boundary into a dedicated non-login publisher identity,
   root-owned boundary, or equivalently isolated service boundary;
2. expose to Hermes runtime only one narrow publication operation, not raw key
   readability and not arbitrary Git/SSH execution as the privileged identity;
3. accept only immutable publication inputs such as expected base SHA, expected
   publication commit SHA, category and date;
4. independently verify repository identity, exact parent/base relation, exact
   commit subject, exact generated-content path allowlist and absence of path
   traversal/symlink surprises;
5. re-read remote `main` immediately before the network write and require it to
   equal the expected base;
6. use an explicit non-forced push of only the expected commit to
   `refs/heads/main` and verify the exact remote SHA afterward;
7. keep all secret paths, key bytes, tokens and SSH command details out of
   public logs, GitHub issues and argv where avoidable;
8. provide a tested rollback/recovery path before the current shared-UID
   credential copy is removed or revoked.

The narrow helper may deliberately allow arbitrary bytes inside the three
approved generated publication files because writing those files is the
publisher capability. It must not permit code/config/workflow paths or arbitrary
refspecs/remotes/commands.

## Production migration sequence

Credential migration is intentionally staged:

1. source-review and CI-test the isolated helper with synthetic repositories and
   no production network write;
2. install the helper/isolated credential while the current publisher remains
   available for rollback;
3. prove allowed and forbidden synthetic commit cases under the installed
   boundary;
4. perform one separately approved real generated-content publication canary;
5. switch `sync_generated_content.sh` to the narrow operation and verify normal
   publication plus post-push reconciliation;
6. prove unrelated shared-UID processes cannot read or directly use the new
   credential;
7. only then remove/rotate/revoke the obsolete shared-UID credential copy;
8. record the final recovery and credential-rotation procedure before closing
   issue #95.

No merge of this document authorizes any credential, ruleset, SSH, service-user,
production or database mutation.

`HERMES_TECH_DEPLOY_REQUIRED=no`

`RPI5_MAIN_CHANGE_REQUIRED=yes` — host implementation owner is
`rozkalnsandris/RPi5_main#110`.
