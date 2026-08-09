#!/usr/bin/env bash
# Network-free validation after dependencies and pinned tools are installed.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
export HERMES_TECH_ROOT="$ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1

for command_name in python git bash shellcheck hugo rsync; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "KĻŪDA: CI trūkst komandas '$command_name'" >&2
        exit 1
    }
done

python - <<'PY'
import sys
if sys.version_info[:2] != (3, 11):
    raise SystemExit(f"KĻŪDA: vajag Python 3.11, atrasts {sys.version.split()[0]}")
print("Python:", sys.version.split()[0])
PY

python tools/check_dependency_sync.py
python tools/check_repository_hygiene.py
python - <<'PY_BOOTSTRAP'
from importlib.metadata import version
expected = {"pip": "26.1.2", "setuptools": "83.0.0"}
actual = {name: version(name) for name in expected}
if actual != expected:
    raise SystemExit(f"KĻŪDA: packaging tool drift: expected={expected}, actual={actual}")
print(f"Packaging tools OK: {actual}")
PY_BOOTSTRAP
python -m pip check

# Emit one deterministic deploy-impact result for the exact CI source revision.
# GitHub's first push to a ref can expose an all-zero `before`; workflow_dispatch
# may expose no base at all. In both cases, classify the target against its
# direct parent. The workflow checks out full history so PR base/head SHAs are
# always available without network access inside tools/ci.sh.
deploy_target=${HERMES_TECH_SOURCE_REVISION:-HEAD}
deploy_base=${HERMES_TECH_BASE_REVISION:-}
if [[ -z "$deploy_base" || "$deploy_base" =~ ^0{40}$ ]]; then
    deploy_base="${deploy_target}^"
fi
printf 'Deploy impact for %s..%s\n' "$deploy_base" "$deploy_target"
python tools/classify_deploy_impact.py \
    --base "$deploy_base" \
    --target "$deploy_target"

python - <<'PY'
from pathlib import Path
import subprocess

root = Path.cwd()
proc = subprocess.run(
    ["git", "ls-files", "-z", "*.py"],
    stdout=subprocess.PIPE,
    check=True,
)
paths = [raw.decode("utf-8") for raw in proc.stdout.split(b"\0") if raw]
for relative in paths:
    path = root / relative
    compile(path.read_text(encoding="utf-8"), relative, "exec")
print(f"Python syntax OK: {len(paths)} tracked files")
PY

python - <<'PY'
import collector
import digest
import format_digest
import hermes_runtime
import ogcard

assert callable(collector.main)
assert callable(digest.main)
assert callable(digest.send_telegram)
assert callable(format_digest.main)
assert callable(ogcard.main)
print("Python imports OK")
PY

python -m unittest discover -s tests -p 'test_*.py' -v

while IFS= read -r -d '' test_script; do
    printf 'Running %s\n' "$test_script"
    bash "$test_script"
done < <(find tests -maxdepth 1 -type f -name 'test_*.sh' -print0 | sort -z)

mapfile -d '' shell_files < <(git ls-files -z '*.sh')
if (( ${#shell_files[@]} == 0 )); then
    echo "KĻŪDA: nav atrasts neviens Bash fails" >&2
    exit 1
fi
for shell_file in "${shell_files[@]}"; do
    bash -n "$shell_file"
done
shellcheck --severity=error --external-sources "${shell_files[@]}"
printf 'Bash syntax + ShellCheck OK: %d files\n' "${#shell_files[@]}"

python tools/scan_secrets.py --self-test
python tools/scan_secrets.py "$ROOT"

build_root=$(mktemp -d)
cleanup() {
    rm -rf "$build_root"
}
trap cleanup EXIT
mkdir -p "$build_root/cache" "$build_root/public"
hugo_log="$build_root/hugo.log"
HUGO_CACHEDIR="$build_root/cache" \
    hugo --source "$ROOT/site" \
    --destination "$build_root/public" \
    --cleanDestinationDir \
    --minify \
    --noBuildLock \
    --logLevel info \
    --panicOnWarning \
    2>&1 | tee "$hugo_log"
[[ -s "$build_root/public/index.html" ]] || {
    echo "KĻŪDA: Hugo pagaidu būvē nav index.html" >&2
    exit 1
}
if grep -Eiq 'deprecat(e|ed|ion|ions)' "$hugo_log"; then
    echo "KĻŪDA: Hugo build log satur deprecation paziņojumu" >&2
    grep -Ei 'deprecat(e|ed|ion|ions)' "$hugo_log" >&2 || true
    exit 1
fi

source_revision=${HERMES_TECH_SOURCE_REVISION:-$(git rev-parse HEAD)}
python tools/capture_site_baseline.py "$build_root/public" \
    --source-revision "$source_revision" \
    --hugo-version "$(hugo version)" \
    --require-zero-scripts \
    | tee "$build_root/site-baseline.json"

python tools/check_site_images.py "$build_root/public" \
    --root "$ROOT" \
    --require-no-content-images \
    --require-local-dimensions \
    --require-alt \
    | tee "$build_root/site-images.json"

if ! git diff --quiet -- . || ! git diff --cached --quiet -- .; then
    echo "KĻŪDA: CI validācija mainīja tracked failus" >&2
    git status --short >&2
    exit 1
fi
status=$(git status --porcelain --untracked-files=all)
if [[ -n "$status" ]]; then
    echo "KĻŪDA: CI validācija atstāja failus darba kokā" >&2
    printf '%s\n' "$status" >&2
    exit 1
fi

echo "Hermes Tech CI PASS"
