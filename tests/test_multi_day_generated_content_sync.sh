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

write_digest() {
    local repo=$1
    local date=$2
    local category=$3
    mkdir -p "$repo/digests"
    printf '%s %s\n' "$date" "$category" > "$repo/digests/$date-$category.md"
}

remote=$(new_remote multi-day)
prod="$TMP/multi-day-prod"
clone_remote "$remote" "$prod"

for spec in \
    '2026-08-26|ai' \
    '2026-08-26|devops' \
    '2026-08-27|agents' \
    '2026-08-27|ai' \
    '2026-08-27|devops' \
    '2026-08-28|agents' \
    '2026-08-28|ai' \
    '2026-08-28|devops' \
    '2026-08-29|agents' \
    '2026-08-29|ai' \
    '2026-08-29|devops'; do
    IFS='|' read -r date category <<<"$spec"
    write_digest "$prod" "$date" "$category"
done

base=$(cd "$prod" && bash "$SYNC_SCRIPT" preflight \
    devops 2026-08-26 digests/2026-08-26-devops.md)

mkdir -p "$prod/site/content/digest" "$prod/site/static/og"
printf 'devops content\n' > "$prod/site/content/digest/2026-08-26.md"
printf 'devops og\n' > "$prod/site/static/og/2026-08-26-devops.png"
git -C "$prod" add -- \
    digests/2026-08-26-devops.md \
    site/content/digest/2026-08-26.md \
    site/static/og/2026-08-26-devops.png

(cd "$prod" && bash "$SYNC_SCRIPT" verify-index \
    devops 2026-08-26 digests/2026-08-26-devops.md "$base")

git -C "$prod" commit --quiet -m 'Publish devops digest 2026-08-26'
head=$(git -C "$prod" rev-parse HEAD)
(cd "$prod" && bash "$SYNC_SCRIPT" sync \
    devops 2026-08-26 digests/2026-08-26-devops.md "$base" "$head")

remote_head=$(git --git-dir="$remote" rev-parse refs/heads/main)
[[ "$remote_head" == "$head" ]] || fail 'multi-day publication remote SHA mismatch'

mapfile -t changed_paths < <(
    git --git-dir="$remote" diff-tree --no-commit-id --name-only -r "$head" | sort
)
expected_paths=(
    'digests/2026-08-26-devops.md'
    'site/content/digest/2026-08-26.md'
    'site/static/og/2026-08-26-devops.png'
)
mapfile -t expected_paths < <(printf '%s\n' "${expected_paths[@]}" | sort)
[[ "${changed_paths[*]}" == "${expected_paths[*]}" ]] || \
    fail "publication commit leaked backlog paths: ${changed_paths[*]}"

remaining_count=$(git -C "$prod" ls-files --others --exclude-standard 'digests/*.md' | wc -l)
[[ "$remaining_count" -eq 10 ]] || \
    fail "expected 10 pending digests after one publication, observed=$remaining_count"
[[ -f "$prod/digests/2026-08-29-agents.md" ]] || \
    fail 'later-day pending digest was lost'

next_base=$(cd "$prod" && bash "$SYNC_SCRIPT" preflight \
    ai 2026-08-26 digests/2026-08-26-ai.md)
[[ "$next_base" == "$head" ]] || fail 'next backlog item did not preflight from current remote SHA'

printf 'unrelated\n' > "$prod/notes.txt"
if (cd "$prod" && bash "$SYNC_SCRIPT" preflight \
    ai 2026-08-26 digests/2026-08-26-ai.md \
    >/dev/null 2>"$TMP/unrelated.err"); then
    fail 'unrelated path was accepted alongside multi-day backlog'
fi
grep -q 'ārpus atļautā digest backlog: notes.txt' "$TMP/unrelated.err" || \
    fail 'unrelated path rejection did not identify the path'

remote=$(new_remote bounded)
prod="$TMP/bounded-prod"
clone_remote "$remote" "$prod"
mkdir -p "$prod/digests"
for day in {01..31}; do
    printf 'pending\n' > "$prod/digests/2026-07-$day-ai.md"
done
printf 'pending\n' > "$prod/digests/2026-08-01-ai.md"

if (cd "$prod" && bash "$SYNC_SCRIPT" preflight \
    ai 2026-07-01 digests/2026-07-01-ai.md \
    >/dev/null 2>"$TMP/bounded.err"); then
    fail '32-date digest backlog was accepted'
fi
grep -q 'pending digest backlog aptver pārāk daudz datumus: 32 > 31' \
    "$TMP/bounded.err" || fail 'backlog bound rejection did not report 32 > 31'

printf 'PASS: multi-day digest backlog is bounded, preserved, and isolated from publication commits\n'