# Development environment and CI

Hermes Tech has one declared, reproducible validation contract. It never needs
production credentials or access to the Raspberry Pi.

## Supported toolchain

- Python `3.11.9` (`>=3.11,<3.12` runtime contract)
- Hugo Extended `0.111.3`
- exact packaging-tool pins from `requirements-bootstrap.txt`
- exact Python package pins from `requirements.txt`
- Bash, Git, rsync, and ShellCheck

`pyproject.toml` declares the supported Python, Hugo, pip, setuptools, and direct
runtime dependencies. `requirements-bootstrap.txt` fixes the packaging
toolchain. `requirements.txt` is the fully pinned runtime environment,
including transitive packages. `requirements-dev.txt` inherits that lock; the
current tests use Python's standard-library `unittest` runner and therefore add
no extra Python packages.

`tools/check_dependency_sync.py` fails when the Python or dependency
declarations drift apart.

## Local setup

From an isolated clone or worktree:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --requirement requirements-bootstrap.txt
python -m pip install --no-build-isolation --requirement requirements-dev.txt

bash tools/install_hugo.sh "$PWD/.tools/bin"
export PATH="$PWD/.tools/bin:$PATH"

# Debian/Ubuntu host dependency:
sudo apt-get install rsync shellcheck

bash tools/ci.sh
```

The Hugo installer downloads the exact official Extended release and verifies
the archive against the checksum file published with that same GitHub release.
It supports Linux amd64 and arm64.

## What `tools/ci.sh` checks

The command is network-free after dependencies and Hugo have been installed. It
performs:

1. Python, packaging-tool, and dependency declaration consistency;
2. `pip check`;
3. syntax compilation of every tracked Python file without writing bytecode;
4. imports of the operational Python entrypoints;
5. all `unittest` tests and every `tests/test_*.sh` suite;
6. Bash syntax and ShellCheck at error severity;
7. a redacted scan of every Git-tracked text file for credential patterns;
8. a Hugo production build into a temporary directory outside `site/public`;
9. a final proof that tracked, staged, and untracked repository state stayed
   clean.

The secret scanner reports only file, line, rule name, and `[REDACTED]`; it
never prints the candidate secret value.

## GitHub Actions safety boundary

`.github/workflows/ci.yml` has read-only repository permissions and does not
reference repository secrets. It never calls DeepSeek, Telegram, RSS feeds,
production hosts, SSH, Docker, Cloudflare, deployment scripts, or the
production database. Dependency and official tool downloads happen only during
setup; repository validation itself is network-free.

The Hugo output is written below the runner's temporary directory and is never
committed.

## Main branch policy

After this workflow has produced a stable successful check on its pull request
and is merged, repository administration must be updated to:

- require the `CI / validate` check for code pull requests;
- require pull requests for normal code and configuration changes;
- disable force-push and branch deletion;
- keep squash merge as the code merge method;
- disable merge commits and rebase merges;
- preserve the separately documented generated-content fast-forward publication
  path from `docs/generated-content-sync.md`.

The generated-content path is not permission to bypass review for human code.
Production deployment remains a separate explicit operation after merge.
