# GitHub `main` policy activation

This runbook completes the repository-administration part of issue #2. It is
separate from code merge and production deployment. Running it changes GitHub
repository settings only; it does not connect to the RPi5, publish a digest, or
deploy the site.

## Required policy

Hermes Tech needs two different trust paths:

- human code, configuration, template, and documentation changes must use a
  pull request, pass `validate`, and be squash-merged;
- the production publisher may fast-forward one tightly validated generated
  digest commit directly to `main`.

A single ruleset with a publisher bypass would be unsafe because the bypass
would also permit branch deletion and force-push. The configurator therefore
creates two layered repository rulesets.

### `Hermes Tech main integrity`

This ruleset has **no bypass actors** and applies to `refs/heads/main`.
It enforces:

- branch deletion blocked;
- non-fast-forward updates blocked;
- linear history required.

The production publisher cannot bypass these rules.

### `Hermes Tech code PR gate`

This ruleset applies to `refs/heads/main` and enforces:

- changes enter through a pull request;
- only squash merge is allowed;
- the `validate` status check must pass against the latest `main`;
- review threads must be resolved.

The only bypass actor is the repository's deploy-key actor. GitHub represents
that actor without a key-specific ID, so the preflight requires exactly one
write-enabled deploy key and requires its title to match the supplied value.
Read-only deploy keys do not create a push path.

Repository merge settings are also changed so merge commits and rebase merges
are disabled, squash merge is enabled, and merged PR branches are deleted.

## GitHub account requirement

The repository is private. Repository rulesets for private repositories require
a GitHub plan that supports them. The API preflight fails without mutation if
the account or token cannot read repository rulesets.

Use a fine-grained personal access token or GitHub App user token with:

- repository Administration: read and write;
- Actions: read;
- Metadata: read.

Store the token only in the process environment. Never place it in `.env`, a
script, a shell history entry, a log archive, or the repository.

## Production identity preflight

Before activation, confirm that the production checkout pushes through the
single intended write-enabled deploy key. The configurator intentionally fails
if:

- no write-enabled deploy key exists;
- more than one write-enabled deploy key exists;
- the write-enabled key title differs from the expected title;
- classic branch protection already targets `main`;
- an unmanaged active ruleset also targets `main`;
- `main` moved from the explicitly supplied SHA;
- the `validate` check is not successful on that exact SHA.

Do not replace this with a broad repository-admin bypass. The direct publisher
identity must remain narrower than the human administrator identity.

## Read-only preflight

Run from a clean checkout containing this tool after its PR has been merged.
Resolve the exact current `main` SHA immediately before the preflight.

```bash
export GH_TOKEN="$(gh auth token)"
REPOSITORY="rozkalnsandris/hermes-tech"
MAIN_SHA="$(git ls-remote --exit-code origin refs/heads/main | awk '{print $1}')"
DEPLOY_KEY_TITLE="<exact GitHub deploy-key title>"

python tools/configure_github_main_policy.py preflight \
  --repository "$REPOSITORY" \
  --expected-main-sha "$MAIN_SHA" \
  --deploy-key-title "$DEPLOY_KEY_TITLE" \
  --status-check validate
```

The command is read-only. Preserve its JSON output as evidence. Do not continue
if any precondition fails.

## Apply

The apply command requires a confirmation bound to both repository and exact
`main` SHA. It repeats the entire preflight before the first write.

```bash
python tools/configure_github_main_policy.py apply \
  --repository "$REPOSITORY" \
  --expected-main-sha "$MAIN_SHA" \
  --deploy-key-title "$DEPLOY_KEY_TITLE" \
  --status-check validate \
  --confirm "APPLY $REPOSITORY@$MAIN_SHA"
```

Write order is deliberate:

1. create or update the no-bypass integrity ruleset;
2. create or update the deploy-key-bypassed PR/CI ruleset;
3. disable non-squash repository merge methods;
4. read everything back and verify the exact policy.

The operation is idempotent. A retry with the same still-current SHA updates the
same two named rulesets rather than creating additional policy layers.

## Independent verification

Run verification after apply and after any later repository-administration
change:

```bash
python tools/configure_github_main_policy.py verify \
  --repository "$REPOSITORY" \
  --expected-main-sha "$MAIN_SHA" \
  --deploy-key-title "$DEPLOY_KEY_TITLE" \
  --status-check validate
```

Successful output must contain:

- `"verified": true`;
- the exact repository and `main` SHA;
- the expected deploy-key ID and title;
- both managed ruleset IDs;
- merge settings with only squash enabled.

Attach the preflight, apply, and verify output to issue #2 before closing it.
Do not include the token or shell environment.

## Publisher activation boundary

GitHub policy activation does not prove that the RPi5 checkout is using the
expected deploy key. A controlled generated-content publication canary is a
separate production action and requires separate approval.

The publisher remains constrained by `sync_generated_content.sh`: exact branch,
exact base SHA, exact commit parent and subject, exact changed-path allowlist,
second pre-push fetch, plain fast-forward push, and post-push SHA equality.
The no-bypass integrity ruleset independently rejects deletion, force-push, and
merge commits even for the deploy-key actor.

## Failure and recovery

The configurator never deletes rulesets or weakens an unmanaged policy.

- A preflight failure performs no writes.
- If apply stops after the integrity ruleset, direct fast-forward pushes remain
  possible but destructive history changes are blocked.
- If apply stops after both rulesets, run `verify` before taking any action.
- Do not delete or disable either managed ruleset as an automatic recovery.
- Do not enable merge commits or rebase merge as a workaround.
- Preserve command output and inspect the exact API error before an explicitly
  approved administrative correction.
