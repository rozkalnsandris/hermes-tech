#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail 'run removal as the andris user, not root'

REPO='rozkalnsandris/hermes-tech'
RUNNER_NAME='rpi5-hermes-tech-release'
RUNNER_SERVICE='actions.runner.rozkalnsandris-hermes-tech.rpi5-hermes-tech-release.service'
RUNNER_DIR='/home/github-tech-runner/actions-runner'
OLD_SUDOERS='/etc/sudoers.d/hermes-tech-github-deploy'
PRIMARY='/home/andris/hermes-tech'
SOURCE='/home/andris/hermes-tech-worktrees/release-control'

for command_name in bash gh git grep python3 rm sudo systemctl; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done
systemctl is-active --quiet hermes-tech-pull-deploy.timer || fail 'replacement pull-deploy timer is not active'

git -C "$SOURCE" fetch --prune origin main
REMOTE_SHA=$(git -C "$SOURCE" rev-parse refs/remotes/origin/main)
PRODUCTION_SHA=$(git -C "$PRIMARY" rev-parse HEAD)
[[ "$PRODUCTION_SHA" == "$REMOTE_SHA" ]] || fail 'production is not exact origin/main; refuse runner removal'

RUNNERS_JSON=$(gh api -H 'Accept: application/vnd.github+json' "repos/$REPO/actions/runners?per_page=100")
RUNNER_ID=$(python3 -c '
import json, sys
name = sys.argv[1]
rows = [row for row in json.load(sys.stdin).get("runners", []) if row.get("name") == name]
if len(rows) != 1:
    raise SystemExit(f"expected exactly one matching runner, observed={len(rows)}")
print(rows[0]["id"])
' "$RUNNER_NAME" <<<"$RUNNERS_JSON") || fail 'could not resolve exactly one Hermes Tech runner'

if systemctl list-unit-files "$RUNNER_SERVICE" --no-legend 2>/dev/null | grep -Fq "$RUNNER_SERVICE"; then
    sudo systemctl disable --now "$RUNNER_SERVICE" || true
fi

gh api --method DELETE "repos/$REPO/actions/runners/$RUNNER_ID"

if [[ -x "$RUNNER_DIR/svc.sh" ]]; then
    sudo bash -c "cd '$RUNNER_DIR' && ./svc.sh uninstall" >/dev/null 2>&1 || true
fi
sudo rm -rf -- "$RUNNER_DIR"
sudo rm -f -- "$OLD_SUDOERS"
sudo systemctl daemon-reload

RUNNERS_JSON=$(gh api -H 'Accept: application/vnd.github+json' "repos/$REPO/actions/runners?per_page=100")
python3 -c '
import json, sys
name = sys.argv[1]
rows = [row for row in json.load(sys.stdin).get("runners", []) if row.get("name") == name]
if rows:
    raise SystemExit("Hermes Tech self-hosted runner is still registered")
' "$RUNNER_NAME" <<<"$RUNNERS_JSON" || fail 'runner deregistration verification failed'

printf 'SELF_HOSTED_RUNNER_REMOVAL_RESULT=PASS\n'
printf 'RUNNER_REGISTERED=false\n'
printf 'PULL_DEPLOY_TIMER_ACTIVE=%s\n' "$(systemctl is-active hermes-tech-pull-deploy.timer)"
printf 'PRODUCTION_SHA=%s\n' "$PRODUCTION_SHA"
