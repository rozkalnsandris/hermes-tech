# Runtime root and dry-run contract

Hermes Tech has one canonical runtime-root selector:

```bash
HERMES_TECH_ROOT=/absolute/path/to/hermes-tech
```

When the variable is unset or empty, every entrypoint keeps the production default:

```text
$HOME/hermes-tech
```

Relative roots are rejected with exit code `2`. The selector does not create the root and does not silently fall back when an explicit value is invalid.

## Derived paths

Python entrypoints derive these paths from the selected root:

| Purpose | Path |
|---|---|
| SQLite database | `data/hermes.db` |
| logs | `logs/` |
| routing runs | `data/runs/` |
| generated digests | `digests/` |
| editorial context | `editorial/` |
| Hugo project | `site/` |
| environment file | `.env` |
| feed list | `feeds.txt` |
| runtime Python | `venv/bin/python` |

`collector.py`, `digest.py`, and `ogcard.py` are stable entrypoints. Their existing implementations live in `collector_core.py`, `digest_core.py`, and `ogcard_core.py`; the entrypoints configure the selected root and preserve the public helper API used by operational scripts.

The shell entrypoints `run_collector.sh`, `run_digests.sh`, and `publish.sh` use the same variable, reject relative paths, and export the resolved selection to child Python processes.

## Isolated worktree usage

An isolated checkout or worktree can be selected without changing production:

```bash
export HERMES_TECH_ROOT=/home/andris/hermes-tech-worktrees/issue-4

"$HERMES_TECH_ROOT/venv/bin/python" \
  "$HERMES_TECH_ROOT/digest.py" validate

"$HERMES_TECH_ROOT/venv/bin/python" \
  "$HERMES_TECH_ROOT/digest.py" digest devops --dry-run

bash "$HERMES_TECH_ROOT/run_collector.sh" --check
bash "$HERMES_TECH_ROOT/run_digests.sh" --check
```

The selected root must contain the runtime files needed by the command. Publication additionally retains the generated-content synchronization rules: it must run in an appropriate Git checkout and satisfy the fast-forward-only `main` policy.

## True digest dry-run

```bash
digest.py digest <devops|ai|agents> --dry-run
```

Dry-run still performs the real candidate selection, model request, quality checks, selected-ID reconciliation, and canonical source restoration. It needs `DEEPSEEK_API_KEY`, because it intentionally tests real generation.

Before generation, the entrypoint creates a temporary runtime and takes an SQLite online backup of `data/hermes.db`. During generation it redirects:

- the database to the temporary backup;
- `logs/digest.log` to a temporary log;
- the generated digest directory to a temporary directory.

The generated Markdown is printed between explicit stdout markers and the temporary runtime is removed afterward. The selected root receives no final digest, log, content, DB, Git index, or Git history change. This also prevents SQLite journal/WAL side effects in the selected root.

`--dry-run` accepts no extra flags. It never invokes `publish.sh`.

## Validation credentials

```bash
digest.py validate
```

Validation is local and does not require `.env` or `DEEPSEEK_API_KEY`. It reads the selected root's routing manifest and returns the dedicated validation exit code when a conflict is found. Existing production Telegram reporting remains available only when the selected root has Telegram settings and validation has an error to report.

## Exit codes

| Code | Meaning |
|---:|---|
| `0` | success |
| `1` | operational failure |
| `2` | invalid CLI usage or invalid runtime-root configuration |
| `3` | validation failure |
| `75` | lock contention in a shell runner |
| `76` | generated-content Git synchronization failure after publication |
| `124` | timeout command limit reached |
| `130` | interrupted with SIGINT |
| `143` | terminated with SIGTERM |

Shell runners may collapse a child validation or category failure into their overall pipeline failure code `1`; the direct `digest.py validate` contract remains `3`.

## Verification

The network-free contract tests use a temporary Git repository, temporary SQLite database, and fake core modules:

```bash
python3 tests/test_runtime_root_contract.py
bash tests/test_shell_runtime_root.sh
```

They prove that:

- an explicit root never resolves to `$HOME/hermes-tech`;
- the production default remains unchanged when no override is supplied;
- validation runs without API credentials;
- dry-run leaves filesystem hashes, the database bytes, and `git status` unchanged;
- dynamic loading still exposes `send_telegram` for the pipeline summary;
- invalid usage, validation failure, operational failure, and lock contention use stable codes;
- shell `--check` modes work against an isolated root.
