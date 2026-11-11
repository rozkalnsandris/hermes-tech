#!/usr/bin/env bash
# Isolated Git push + readiness CAS, without touching production or host state.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SYNC="$ROOT/sync_generated_content.sh"
HELPER="$ROOT/tools/pull-deploy/deploy_readiness.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export HERMES_GIT_SYNC_TIMEOUT_SECONDS=5
export HERMES_GIT_SYNC_PUSH_ATTEMPTS=1
export GIT_AUTHOR_NAME='Hermes CI'
export GIT_AUTHOR_EMAIL='hermes-ci@example.invalid'
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export HERMES_TECH_DEPLOY_STATE_ROOT="$TMP/state"

remote="$TMP/remote.git"
seed="$TMP/seed"
prod="$TMP/production"
git init --bare --quiet "$remote"
git init --quiet --initial-branch=main "$seed"
git -C "$seed" config user.name "$GIT_AUTHOR_NAME"
git -C "$seed" config user.email "$GIT_AUTHOR_EMAIL"
mkdir -p "$seed/tools/pull-deploy"
cp "$HELPER" "$seed/tools/pull-deploy/deploy_readiness.py"
printf 'baseline\n' > "$seed/base.txt"
git -C "$seed" add -- base.txt tools/pull-deploy/deploy_readiness.py
git -C "$seed" commit --quiet -m 'Initial fixture with readiness helper'
git -C "$seed" remote add origin "$remote"
git -C "$seed" push --quiet origin main
git --git-dir="$remote" symbolic-ref HEAD refs/heads/main

git clone --quiet "$remote" "$prod"
git -C "$prod" config user.name "$GIT_AUTHOR_NAME"
git -C "$prod" config user.email "$GIT_AUTHOR_EMAIL"
base=$(git -C "$prod" rev-parse HEAD)
python3 "$prod/tools/pull-deploy/deploy_readiness.py" record \
    --state-root "$HERMES_TECH_DEPLOY_STATE_ROOT" \
    --env-file "$TMP/no-telegram.env" \
    --reason CURRENT --deploy-impact NO_DEPLOY \
    --target-sha "$base" --production-sha "$base" --no-notify >/dev/null

date=2026-08-05
mkdir -p "$prod/digests"
for category in devops ai agents; do
    printf '%s draft\n' "$category" > "$prod/digests/$date-$category.md"
done

for category in devops ai agents; do
    section="$category"
    [[ "$category" != devops ]] || section=digest
    draft="digests/$date-$category.md"
    expected_base=$(cd "$prod" && bash "$SYNC" preflight "$category" "$date" "$draft")
    [[ "$expected_base" == "$base" ]]
    mkdir -p "$prod/site/content/$section" "$prod/site/static/og"
    printf '%s content\n' "$category" > "$prod/site/content/$section/$date.md"
    printf 'og card\n' > "$prod/site/static/og/$date-$category.png"
    git -C "$prod" add -- "$draft" \
        "site/content/$section/$date.md" "site/static/og/$date-$category.png"
    (cd "$prod" && bash "$SYNC" verify-index "$category" "$date" "$draft" "$base")
    git -C "$prod" commit --quiet -m "Publish $category digest $date"
    new_sha=$(git -C "$prod" rev-parse HEAD)
    output=$(cd "$prod" && bash "$SYNC" sync \
        "$category" "$date" "$draft" "$base" "$new_sha")
    grep -q 'READINESS_PUBLISHED_ADVANCE=CHANGED' <<<"$output"
    grep -q 'HERMES_GIT_SYNC_OK' <<<"$output"
    state_sha=$(python3 - "$HERMES_TECH_DEPLOY_STATE_ROOT/readiness.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["reason"] == "CURRENT"
assert payload["deploy_impact"] == "NO_DEPLOY"
assert payload["schema_version"] == 2
assert payload["main_sha"] == payload["target_sha"] == payload["production_sha"]
print(payload["target_sha"])
PY
)
    [[ "$state_sha" == "$new_sha" ]]
    [[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$new_sha" ]]
    base="$new_sha"
done
[[ "$(stat -c '%a' "$HERMES_TECH_DEPLOY_STATE_ROOT/readiness.json")" == 600 ]]

# A blocked state must stop the next publication *before* generating a commit.
python3 "$prod/tools/pull-deploy/deploy_readiness.py" record \
    --state-root "$HERMES_TECH_DEPLOY_STATE_ROOT" \
    --env-file "$TMP/no-telegram.env" \
    --reason WAIT_CI --deploy-impact AUTO_DEPLOY_SAFE \
    --target-sha "$base" --production-sha "$base" --no-notify >/dev/null
next_date=2026-08-06
printf 'new draft\n' > "$prod/digests/$next_date-devops.md"
if (cd "$prod" && bash "$SYNC" preflight devops "$next_date" \
    "digests/$next_date-devops.md" >"$TMP/blocked.out" 2>"$TMP/blocked.err"); then
    echo 'blocked readiness unexpectedly passed preflight' >&2
    exit 1
fi
grep -q 'readiness is not CURRENT NO_DEPLOY' "$TMP/blocked.err"
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$base" ]]

# Returning to CURRENT for an earlier SHA must not bypass the exact-base gate.
python3 "$prod/tools/pull-deploy/deploy_readiness.py" record \
    --state-root "$HERMES_TECH_DEPLOY_STATE_ROOT" \
    --env-file "$TMP/no-telegram.env" \
    --reason CURRENT --deploy-impact NO_DEPLOY \
    --target-sha "$(git -C "$prod" rev-parse HEAD^)" \
    --production-sha "$(git -C "$prod" rev-parse HEAD^)" --no-notify >/dev/null
if (cd "$prod" && bash "$SYNC" preflight devops "$next_date" \
    "digests/$next_date-devops.md" >"$TMP/stale.out" 2>"$TMP/stale.err"); then
    echo 'stale readiness unexpectedly passed preflight' >&2
    exit 1
fi
grep -q 'readiness is not CURRENT NO_DEPLOY' "$TMP/stale.err"
echo 'generated-content readiness autoreconcile integration PASS'
