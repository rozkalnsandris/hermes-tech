# Generated digest Git synchronization

Hermes Tech uses two separate GitHub workflows because code changes and production-generated digest content have different trust boundaries.

## Policy

### Code and configuration

Code, templates, documentation, and operational changes use the normal engineering workflow:

1. GitHub issue;
2. task-specific branch and isolated worktree;
3. Draft pull request;
4. read-only validation and CI;
5. explicit review and squash merge;
6. separate production deployment approval.

A merge never authorizes a deployment.

### Production-generated digests

The production publisher may create one direct commit on `main` only for the current category and date. The commit is restricted to exactly these paths:

- the selected `digests/<date>[-<category>].md` source file;
- `site/content/<section>/<date>.md`;
- `site/static/og/<date>-<category>.png`.

`sync_generated_content.sh` permits a plain fast-forward push only when all of these conditions hold:

- the production checkout is on `main`;
- local `HEAD` and freshly fetched `origin/main` are identical before publication;
- no file is already staged;
- pending working-tree files are limited to the current publication paths plus generated digest-source drafts matching `digests/YYYY-MM-DD[-<category>].md`;
- pending digest-source drafts span at most 31 distinct dates;
- the publication commit is a direct child of the verified preflight SHA;
- its subject is exactly `Publish <category> digest <date>`;
- every changed path is in the current publication allowlist;
- a second fetch immediately before push still resolves `origin/main` to the preflight SHA;
- a post-push fetch proves local and remote SHAs are exactly the publication commit.

The helper never performs merge, rebase, reset, history rewrite, or force-push. Network Git operations are bounded by a timeout and disable interactive credential prompts.

The publisher's `.publish-work.*` directories are transient, ignored Git runtime data. They remain outside the explicit three-path staging allowlist and therefore cannot enter a generated commit.

When branch protection is introduced, the production publication identity may receive a narrowly scoped bypass for these generated-content commits. Force-push and branch deletion must remain blocked. Human code changes continue through pull requests.

### Layered GitHub rulesets

The bypass must not be placed on the same ruleset that protects branch history. GitHub ruleset bypass applies to every rule in that ruleset, so a single combined policy would also let the publisher bypass deletion and non-fast-forward protection.

Hermes Tech therefore uses two repository rulesets targeting `refs/heads/main`:

1. `Hermes Tech main integrity` has no bypass actors and blocks branch deletion, non-fast-forward updates, and non-linear history.
2. `Hermes Tech code PR gate` requires a pull request, permits only squash merge, requires the `validate` check, and requires resolved review threads. Its only bypass actor is the repository deploy-key actor used by the generated-content publisher.

GitHub represents a deploy-key bypass without a key-specific actor ID. Before activation, `tools/configure_github_main_policy.py` therefore requires exactly one write-enabled deploy key and an exact expected key title. Read-only deploy keys cannot push. The configurator also rejects existing classic protection or unmanaged active rulesets targeting `main`, so overlapping policy is never silently accepted.

The complete SHA-bound preflight, apply, independent verification, permissions, and failure procedure is documented in `docs/github-main-policy.md`. GitHub policy activation is repository administration only. Proving that the RPi5 checkout actually uses the intended key and running a generated publication canary remain separate, explicitly approved production actions.

## Publication and failure semantics

The Git preflight runs before Hugo content, live site files, or the database are changed. A dirty checkout, local-ahead state, remote-ahead state, or divergence therefore blocks publication before mutation.

After the site and database commit successfully, GitHub synchronization becomes a separate observable phase. If a remote race, authentication failure, timeout, or policy violation occurs at that point:

- the published site and committed database state are retained;
- the exact local publication commit is retained;
- the command exits with code `76`;
- stderr contains `KĻŪDA: HERMES_GIT_SYNC` or a publication sync error;
- the existing digest runner records the publication failure and reports it through healthcheck/Telegram summary handling.

This deliberately avoids silently discarding content that was already published.

## Recovery states

Always preserve evidence before changing the production checkout:

```bash
git status --short
git log --oneline --decorate --graph -10
git fetch --no-tags origin main
git rev-parse HEAD origin/main
git diff --name-status origin/main...HEAD
```

Never use `git push --force`, `git reset --hard`, or an automatic merge/rebase as a recovery shortcut.

### `LOCAL_AHEAD`

A generated publication commit exists locally but is not on GitHub.

1. Stop new publications.
2. Confirm there is exactly one expected generated commit.
3. Verify its parent is the remote SHA, its subject matches the category/date, and its changed paths are only the three allowed publication paths.
4. Run the helper's `sync` mode with the exact parent and commit SHAs, or use an explicitly approved reconciliation PR when direct synchronization is no longer a fast-forward.
5. Fetch again and verify `HEAD == origin/main`.

### `REMOTE_AHEAD`

GitHub advanced while production has no local generated commit.

1. Preserve any untracked digest drafts outside the checkout if necessary.
2. Inspect the remote commits.
3. Update the production checkout only with an explicitly approved fast-forward operation:

```bash
git fetch --no-tags origin main
git merge --ff-only origin/main
```

4. Restore only the expected pending digest drafts and rerun publication.

### `DIVERGED` or remote race after a local generated commit

Both histories contain unique commits.

1. Stop publication and keep the local generated commit reachable, optionally with a backup ref:

```bash
git branch recovery/generated-content-<date>-<category> <local-commit-sha>
```

2. Do not merge or rebase in the live production checkout.
3. Create an isolated worktree from the current `origin/main`.
4. Cherry-pick the generated commit in that isolated worktree and inspect the exact three-path diff.
5. Reconcile through a Draft PR or another explicitly authorized fast-forward-only procedure.
6. After GitHub is authoritative, verify the publication files by checksum and explicitly fast-forward the production checkout.

## Validation

Run both network-free isolated repository suites:

```bash
bash tests/test_generated_content_sync.sh
bash tests/test_publish_generated_content_integration.sh
```

CI also runs `tests/test_multi_day_generated_content_sync.sh`, which reproduces a multi-day pending digest backlog and verifies that:

- one publication can proceed while later pending digest-source drafts remain untouched;
- the publication commit still contains only its exact three-path allowlist;
- unrelated tracked/untracked content remains rejected;
- more than 31 pending digest dates fail closed.

`test_generated_content_sync.sh` creates temporary local bare remotes and covers:

- publication commit, fast-forward push, and exact SHA verification;
- sibling same-day digest isolation;
- unrelated working-tree rejection;
- concurrent remote changes without commit loss or force-push;
- commit path allowlist enforcement;
- local-ahead publication blocking.

`test_publish_generated_content_integration.sh` executes the real `publish.sh` with an isolated SQLite database and mocked Hugo build. It verifies the complete sequence: generated content, live publication, database commit, exact-path Git commit, fast-forward push, and local/remote SHA equality.

This implementation does not deploy or alter the production checkout. Activation requires a separate reviewed deployment step.
