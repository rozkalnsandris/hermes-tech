#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SYNC_SCRIPT=${1:-"$REPO_ROOT/sync_generated_content.sh"}
[[ -f "$SYNC_SCRIPT" ]] || { echo "missing sync script: $SYNC_SCRIPT" >&2; exit 2; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

export GIT_AUTHOR_NAME='Hermes Test'
export GIT_AUTHOR_EMAIL='hermes-test@example.invalid'
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export HERMES_GIT_SYNC_TIMEOUT_SECONDS=5

pass_count=0

pass() {
    pass_count=$((pass_count + 1))
    printf 'PASS: %s\n' "$1"
}

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

new_remote() {
    local name=$1
    local remote="$TMP/$name.git"
    local seed="$TMP/$name-seed"
    git init --bare --quiet "$remote"
    git init --quiet --initial-branch=main "$seed"
    git -C "$seed" config user.name "$GIT_AUTHOR_NAME"
    git -C "$seed" config user.email "$GIT_AUTHOR_EMAIL"
    printf 'base\n' > "$seed/base.txt"
    git -C "$seed" add -- base.txt
    git -C "$seed" commit --quiet -m 'Initial base'
    git -C "$seed" remote add origin "$remote"
    git -C "$seed" push --quiet -u origin main
    git --git-dir="$remote" symbolic-ref HEAD refs/heads/main
    printf '%s\n' "$remote"
}

clone_remote() {
    local remote=$1
    local destination=$2
    git clone --quiet "$remote" "$destination"
    git -C "$destination" config user.name "$GIT_AUTHOR_NAME"
    git -C "$destination" config user.email "$GIT_AUTHOR_EMAIL"
}

prepare_pending_digests() {
    local repo=$1
    local date=$2
    mkdir -p "$repo/digests"
    printf 'devops\n' > "$repo/digests/$date-devops.md"
    printf 'ai\n' > "$repo/digests/$date-ai.md"
    printf 'agents\n' > "$repo/digests/$date-agents.md"
}

publish_one() {
    local repo=$1
    local category=$2
    local date=$3
    local section=$4
    local base
    base=$(cd "$repo" && bash "$SYNC_SCRIPT" preflight \
        "$category" "$date" "digests/$date-$category.md")
    mkdir -p "$repo/site/content/$section" "$repo/site/static/og"
    printf '%s content\n' "$category" > "$repo/site/content/$section/$date.md"
    printf '%s og\n' "$category" > "$repo/site/static/og/$date-$category.png"
    git -C "$repo" add -- \
        "digests/$date-$category.md" \
        "site/content/$section/$date.md" \
        "site/static/og/$date-$category.png"
    (cd "$repo" && bash "$SYNC_SCRIPT" verify-index \
        "$category" "$date" "digests/$date-$category.md" "$base")
    git -C "$repo" commit --quiet -m "Publish $category digest $date"
    local head
    head=$(git -C "$repo" rev-parse HEAD)
    (cd "$repo" && bash "$SYNC_SCRIPT" sync \
        "$category" "$date" "digests/$date-$category.md" "$base" "$head")
    printf '%s\n' "$head"
}

# 1. Complete publication -> commit -> fast-forward push -> SHA verification.
remote=$(new_remote happy)
prod="$TMP/happy-prod"
clone_remote "$remote" "$prod"
prepare_pending_digests "$prod" 2026-08-05
head=$(publish_one "$prod" devops 2026-08-05 digest | tail -n 1)
remote_head=$(git --git-dir="$remote" rev-parse refs/heads/main)
[[ "$head" == "$remote_head" ]] || fail 'happy path remote SHA mismatch'
git --git-dir="$remote" show "$remote_head:site/content/digest/2026-08-05.md" >/dev/null
[[ -f "$prod/digests/2026-08-05-ai.md" ]] || fail 'pending sibling digest was lost'
[[ -f "$prod/digests/2026-08-05-agents.md" ]] || fail 'pending sibling digest was lost'
if git --git-dir="$remote" cat-file -e \
    "$remote_head:digests/2026-08-05-ai.md" 2>/dev/null; then
    fail 'sibling digest leaked into current publication commit'
fi
pass 'complete publication commit synchronizes and verifies exact SHA'

# 2. Preflight rejects unrelated working-tree changes.
remote=$(new_remote dirty)
prod="$TMP/dirty-prod"
clone_remote "$remote" "$prod"
prepare_pending_digests "$prod" 2026-08-05
printf 'unrelated\n' > "$prod/notes.txt"
if (cd "$prod" && bash "$SYNC_SCRIPT" preflight devops 2026-08-05 \
    digests/2026-08-05-devops.md >/dev/null 2>"$TMP/dirty.err"); then
    fail 'unrelated dirty path was accepted'
fi
grep -q 'neatļauta untracked izmaiņa' "$TMP/dirty.err" || \
    fail 'dirty path rejection did not explain the failure'
pass 'preflight rejects unrelated tracked/untracked content'

# 3. Concurrent remote update fails closed and preserves the local commit.
remote=$(new_remote race)
prod="$TMP/race-prod"
other="$TMP/race-other"
clone_remote "$remote" "$prod"
clone_remote "$remote" "$other"
prepare_pending_digests "$prod" 2026-08-05
base=$(cd "$prod" && bash "$SYNC_SCRIPT" preflight ai 2026-08-05 \
    digests/2026-08-05-ai.md)
mkdir -p "$prod/site/content/ai" "$prod/site/static/og"
printf 'ai content\n' > "$prod/site/content/ai/2026-08-05.md"
printf 'ai og\n' > "$prod/site/static/og/2026-08-05-ai.png"
git -C "$prod" add -- digests/2026-08-05-ai.md \
    site/content/ai/2026-08-05.md site/static/og/2026-08-05-ai.png
(cd "$prod" && bash "$SYNC_SCRIPT" verify-index ai 2026-08-05 \
    digests/2026-08-05-ai.md "$base")
git -C "$prod" commit --quiet -m 'Publish ai digest 2026-08-05'
local_generated=$(git -C "$prod" rev-parse HEAD)
printf 'remote code change\n' > "$other/code.txt"
git -C "$other" add -- code.txt
git -C "$other" commit --quiet -m 'Remote code change'
git -C "$other" push --quiet origin main
remote_after_code=$(git --git-dir="$remote" rev-parse refs/heads/main)
if (cd "$prod" && bash "$SYNC_SCRIPT" sync ai 2026-08-05 \
    digests/2026-08-05-ai.md "$base" "$local_generated" \
    >"$TMP/race.out" 2>"$TMP/race.err"); then
    fail 'concurrent remote change was accepted'
fi
[[ "$(git -C "$prod" rev-parse HEAD)" == "$local_generated" ]] || \
    fail 'local generated commit was discarded'
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$remote_after_code" ]] || \
    fail 'remote history changed after rejected race'
if git --git-dir="$remote" cat-file -e \
    "$remote_after_code:site/content/ai/2026-08-05.md" 2>/dev/null; then
    fail 'generated content was pushed despite remote race'
fi
grep -Eq 'REMOTE_AHEAD|DIVERGED' "$TMP/race.err" || \
    fail 'race failure did not report repository relation'
pass 'concurrent remote update is rejected without force-push or commit loss'

# 4. A commit that contains an unrelated path is rejected before push.
remote=$(new_remote scope)
prod="$TMP/scope-prod"
clone_remote "$remote" "$prod"
prepare_pending_digests "$prod" 2026-08-05
base=$(cd "$prod" && bash "$SYNC_SCRIPT" preflight agents 2026-08-05 \
    digests/2026-08-05-agents.md)
mkdir -p "$prod/site/content/agents" "$prod/site/static/og"
printf 'agents content\n' > "$prod/site/content/agents/2026-08-05.md"
printf 'agents og\n' > "$prod/site/static/og/2026-08-05-agents.png"
printf 'not publication content\n' > "$prod/publish.sh"
git -C "$prod" add -- digests/2026-08-05-agents.md \
    site/content/agents/2026-08-05.md site/static/og/2026-08-05-agents.png \
    publish.sh
git -C "$prod" commit --quiet -m 'Publish agents digest 2026-08-05'
head=$(git -C "$prod" rev-parse HEAD)
if (cd "$prod" && bash "$SYNC_SCRIPT" sync agents 2026-08-05 \
    digests/2026-08-05-agents.md "$base" "$head" \
    >/dev/null 2>"$TMP/scope.err"); then
    fail 'commit with unrelated path was accepted'
fi
grep -q 'commits satur neatļautu ceļu: publish.sh' "$TMP/scope.err" || \
    fail 'scope rejection did not identify the unrelated path'
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$base" ]] || \
    fail 'remote changed after rejected scope violation'
pass 'commit path allowlist blocks unrelated files'

# 5. A local-ahead checkout is detected before another publication starts.
remote=$(new_remote ahead)
prod="$TMP/ahead-prod"
clone_remote "$remote" "$prod"
prepare_pending_digests "$prod" 2026-08-05
git -C "$prod" add -- digests/2026-08-05-devops.md
git -C "$prod" commit --quiet -m 'Publish devops digest 2026-08-05'
if (cd "$prod" && bash "$SYNC_SCRIPT" preflight ai 2026-08-05 \
    digests/2026-08-05-ai.md >/dev/null 2>"$TMP/ahead.err"); then
    fail 'local-ahead checkout was accepted'
fi
grep -q 'stāvoklis=LOCAL_AHEAD' "$TMP/ahead.err" || \
    fail 'local-ahead state was not classified'
pass 'local-ahead state blocks stacking another generated commit'

printf 'All %d generated-content sync tests passed.\n' "$pass_count"
