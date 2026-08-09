#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
RUNTIME="$TMP/isolated-hermes"
mkdir -p "$RUNTIME/venv/bin" "$RUNTIME/tools"
ln -s "$(command -v python3)" "$RUNTIME/venv/bin/python"
cp "$ROOT/collector.py" "$RUNTIME/collector.py"
cp "$ROOT/digest.py" "$RUNTIME/digest.py"
cp "$ROOT/publish.sh" "$RUNTIME/publish.sh"

# This fixture tests runtime-root and lock semantics, not the production
# readiness integration. Provide a fixture-local READY implementation so the
# full digest runner can reach its historical .digest-pipeline.lock boundary.
# No production skip flag or runtime bypass exists.
cat >"$RUNTIME/tools/publication_readiness.py" <<'PY_READY'
#!/usr/bin/env python3
import json
import sys

if len(sys.argv) >= 2 and sys.argv[1] == "check":
    print(json.dumps({
        "ready": True,
        "reason": "CURRENT",
        "target_sha": "a" * 40,
        "production_sha": "a" * 40,
        "detail": "isolated lock-contract fixture",
    }))
    raise SystemExit(0)
raise SystemExit(2)
PY_READY
chmod 700 "$RUNTIME/tools/publication_readiness.py"

for script in run_collector.sh run_digests.sh publish.sh; do
    grep -Fq 'HERMES_TECH_ROOT:-$HOME/hermes-tech' "$ROOT/$script" || {
        echo "FAIL: $script does not preserve production default root" >&2
        exit 1
    }
    bash -n "$ROOT/$script"
done

HERMES_TECH_ROOT="$RUNTIME" bash "$ROOT/run_collector.sh" --check >/dev/null
HERMES_TECH_ROOT="$RUNTIME" bash "$ROOT/run_digests.sh" --check >/dev/null

set +e
HERMES_TECH_ROOT=relative/path bash "$ROOT/run_collector.sh" --check >/dev/null 2>&1
relative_rc=$?
set -e
[[ $relative_rc -eq 2 ]] || {
    echo "FAIL: relative root returned $relative_rc, expected 2" >&2
    exit 1
}

exec 8>"$RUNTIME/.collector.lock"
flock -n 8
set +e
HERMES_TECH_ROOT="$RUNTIME" bash "$ROOT/run_collector.sh" >/dev/null 2>&1
collector_lock_rc=$?
set -e
[[ $collector_lock_rc -eq 75 ]] || {
    echo "FAIL: collector lock returned $collector_lock_rc, expected 75" >&2
    exit 1
}
flock -u 8

exec 7>"$RUNTIME/.digest-pipeline.lock"
flock -n 7
set +e
HERMES_TECH_ROOT="$RUNTIME" bash "$ROOT/run_digests.sh" >/dev/null 2>&1
digest_lock_rc=$?
set -e
[[ $digest_lock_rc -eq 75 ]] || {
    echo "FAIL: digest lock returned $digest_lock_rc, expected 75" >&2
    exit 1
}
flock -u 7

printf 'PASS: shell runners honor isolated roots and stable lock exit code 75\n'
