#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SYNC_SCRIPT=${1:-"$REPO_ROOT/sync_generated_content.sh"}
[[ -f "$SYNC_SCRIPT" ]] || { echo "missing sync script: $SYNC_SCRIPT" >&2; exit 2; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
REAL_GIT=$(command -v git)

export GIT_AUTHOR_NAME='Hermes Test'
export GIT_AUTHOR_EMAIL='hermes-test@example.invalid'
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export HERMES_GIT_SYNC_TIMEOUT_SECONDS=5
export HERMES_GIT_SYNC_PUSH_ATTEMPTS=3
export HERMES_GIT_SYNC_PUSH_RETRY_DELAY_SECONDS=0

new_remote() {
    local name=$1
    local remote="$TMP/$name.git"
    local seed="$TMP/$name-seed"
    "$REAL_GIT" init --bare --quiet "$remote"
    "$REAL_GIT" init --quiet --initial-branch=main "$seed"
    "$REAL_GIT" -C "$seed" config user.name "$GIT_AUTHOR_NAME"
    "$REAL_GIT" -C "$seed" config user.email "$GIT_AUTHOR_EMAIL"
    printf 'base\n' > "$seed/base.txt"
    "$REAL_GIT" -C "$seed" add -- base.txt
    "$REAL_GIT" -C "$seed" commit --quiet -m 'Initial base'
    "$REAL_GIT" -C "$seed" remote add origin "$remote"
    "$REAL_GIT" -C "$seed" push --quiet -u origin main
    "$REAL_GIT" --git-dir="$remote" symbolic-ref HEAD refs/heads/main
    printf '%s\n' "$remote"
}

clone_remote() {
    "$REAL_GIT" clone --quiet "$1" "$2"
    "$REAL_GIT" -C "$2" config user.name "$GIT_AUTHOR_NAME"
    "$REAL_GIT" -C "$2" config user.email "$GIT_AUTHOR_EMAIL"
}

prepare_commit() {
    local repo=$1 date=$2
    mkdir -p "$repo/digests" "$repo/site/content/digest" "$repo/site/static/og"
    printf 'devops\n' > "$repo/digests/$date-devops.md"
    local base
    base=$(cd "$repo" && bash "$SYNC_SCRIPT" preflight devops "$date" "digests/$date-devops.md")
    printf 'content\n' > "$repo/site/content/digest/$date.md"
    printf 'og\n' > "$repo/site/static/og/$date-devops.png"
    "$REAL_GIT" -C "$repo" add -- "digests/$date-devops.md" "site/content/digest/$date.md" "site/static/og/$date-devops.png"
    (cd "$repo" && bash "$SYNC_SCRIPT" verify-index devops "$date" "digests/$date-devops.md" "$base")
    "$REAL_GIT" -C "$repo" commit --quiet -m "Publish devops digest $date"
    printf '%s\t%s\n' "$base" "$("$REAL_GIT" -C "$repo" rev-parse HEAD)"
}

make_git_shim() {
    local dir=$1 mode=$2 state=$3
    mkdir -p "$dir"
    cat > "$dir/git" <<'SHIM'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" == "push" ]]; then
    count=0
    [[ ! -f "$HERMES_TEST_PUSH_STATE" ]] || count=$(cat "$HERMES_TEST_PUSH_STATE")
    count=$((count + 1))
    printf '%s\n' "$count" > "$HERMES_TEST_PUSH_STATE"
    if (( count == 1 )); then
        case "$HERMES_TEST_PUSH_MODE" in
            fail-before-send)
                echo 'Connection closed by 140.82.121.3 port 22' >&2
                exit 128
                ;;
            accept-then-fail)
                "$HERMES_TEST_REAL_GIT" "$@"
                echo 'Connection closed by 140.82.121.3 port 22' >&2
                exit 128
                ;;
            reject)
                printf '!\tHEAD:refs/heads/main\t[remote rejected] (hook declined)\n'
                exit 1
                ;;
        esac
    fi
fi
exec "$HERMES_TEST_REAL_GIT" "$@"
SHIM
    chmod +x "$dir/git"
    export HERMES_TEST_REAL_GIT="$REAL_GIT"
    export HERMES_TEST_PUSH_MODE="$mode"
    export HERMES_TEST_PUSH_STATE="$state"
}

# 1. Exact 2026-08-14 incident shape: first SSH transport failure, second push succeeds.
remote=$(new_remote retry)
repo="$TMP/retry-prod"
clone_remote "$remote" "$repo"
read -r base head < <(prepare_commit "$repo" 2026-08-14)
shim="$TMP/retry-shim"
state="$TMP/retry-count"
make_git_shim "$shim" fail-before-send "$state"
(export PATH="$shim:$PATH"; cd "$repo"; bash "$SYNC_SCRIPT" sync devops 2026-08-14 digests/2026-08-14-devops.md "$base" "$head") >"$TMP/retry.out" 2>"$TMP/retry.err"
[[ "$(cat "$state")" == 2 ]]
[[ "$("$REAL_GIT" --git-dir="$remote" rev-parse refs/heads/main)" == "$head" ]]
grep -q 'HERMES_GIT_SYNC_RETRY attempt=1/3' "$TMP/retry.err"
grep -q 'HERMES_GIT_SYNC_OK' "$TMP/retry.out"

# 2. Ambiguous outcome: server accepted commit but client lost response. Do not push twice.
remote=$(new_remote reconcile)
repo="$TMP/reconcile-prod"
clone_remote "$remote" "$repo"
read -r base head < <(prepare_commit "$repo" 2026-08-15)
shim="$TMP/reconcile-shim"
state="$TMP/reconcile-count"
make_git_shim "$shim" accept-then-fail "$state"
(export PATH="$shim:$PATH"; cd "$repo"; bash "$SYNC_SCRIPT" sync devops 2026-08-15 digests/2026-08-15-devops.md "$base" "$head") >"$TMP/reconcile.out" 2>"$TMP/reconcile.err"
[[ "$(cat "$state")" == 1 ]]
[[ "$("$REAL_GIT" --git-dir="$remote" rev-parse refs/heads/main)" == "$head" ]]
grep -q 'HERMES_GIT_SYNC_RECONCILED' "$TMP/reconcile.err"
grep -q 'HERMES_GIT_SYNC_OK' "$TMP/reconcile.out"

# 3. Explicit rejection is never retried.
remote=$(new_remote reject)
repo="$TMP/reject-prod"
clone_remote "$remote" "$repo"
read -r base head < <(prepare_commit "$repo" 2026-08-16)
shim="$TMP/reject-shim"
state="$TMP/reject-count"
make_git_shim "$shim" reject "$state"
if (export PATH="$shim:$PATH"; cd "$repo"; bash "$SYNC_SCRIPT" sync devops 2026-08-16 digests/2026-08-16-devops.md "$base" "$head") >"$TMP/reject.out" 2>"$TMP/reject.err"; then
    echo 'explicit rejection unexpectedly succeeded' >&2
    exit 1
fi
[[ "$(cat "$state")" == 1 ]]
[[ "$("$REAL_GIT" --git-dir="$remote" rev-parse refs/heads/main)" == "$base" ]]
! grep -q 'HERMES_GIT_SYNC_RETRY' "$TMP/reject.err"

echo 'generated-content transport retry tests passed'
