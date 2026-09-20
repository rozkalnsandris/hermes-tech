#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

SOURCE_WORKTREE='/home/andris/hermes-tech-worktrees/release-control'
PRIMARY='/home/andris/hermes-tech'
STATE_ROOT='/home/andris/.local/state/hermes-tech-main-deploy'
CONTROL_APPROVAL="$STATE_ROOT/approved-control-plane-sha"
NOREPLY_EMAIL='277435981+rozkalnsandris@users.noreply.github.com'
MUTATION_STARTED=false
HEAD_SHA=''

finish() {
    local rc=$?
    trap - EXIT
    if [[ $rc -ne 0 && "$MUTATION_STARTED" == true ]]; then
        local observed_production='UNKNOWN'
        observed_production=$(git -C "$PRIMARY" rev-parse HEAD 2>/dev/null || printf 'UNKNOWN')
        printf 'PULL_DEPLOY_ACTIVATION_RESULT=FAIL_STOP_NO_ROLLBACK\n' >&2
        printf 'SOURCE_SHA=%s\n' "${HEAD_SHA:-UNKNOWN}" >&2
        printf 'PRODUCTION_SHA=%s\n' "$observed_production" >&2
        printf 'ROLLBACK_PERFORMED=false\n' >&2
        printf 'AUTOMATIC_CLEANUP_PERFORMED=false\n' >&2
        printf 'DATABASE_MIGRATIONS_EXECUTED=false\n' >&2
    fi
    exit "$rc"
}
trap finish EXIT

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail 'run activation as the andris user, not root'

for command_name in chmod curl git install sudo systemctl; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done
[[ "$(pwd -P)" == "$SOURCE_WORKTREE" ]] || fail 'activation must run from release-control worktree'

git fetch --prune origin main
HEAD_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse refs/remotes/origin/main)
[[ "$HEAD_SHA" == "$REMOTE_SHA" ]] || fail 'release-control is not exact origin/main'
[[ -z "$(git branch --show-current)" ]] || fail 'release-control must remain detached'
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || fail 'release-control worktree is not clean'

# The first host/control-plane mutation starts here. From this point onward any
# error must preserve the observed state and stop without rollback or cleanup.
MUTATION_STARTED=true
sudo systemctl disable --now hermes-tech-pull-deploy.timer >/dev/null 2>&1
sudo bash ./tools/pull-deploy/install-pull-deploy.sh

# Preserve historical commit SHAs, but keep future local publisher/operator commits private.
git -C "$PRIMARY" config user.email "$NOREPLY_EMAIL"
git -C "$SOURCE_WORKTREE" config user.email "$NOREPLY_EMAIL"
[[ "$(git -C "$PRIMARY" config user.email)" == "$NOREPLY_EMAIL" ]] || fail 'production Git noreply email was not configured'
[[ "$(git -C "$SOURCE_WORKTREE" config user.email)" == "$NOREPLY_EMAIL" ]] || fail 'release-control Git noreply email was not configured'

install -d -m 0700 "$STATE_ROOT"
printf '%s\n' "$HEAD_SHA" >"$CONTROL_APPROVAL"
chmod 0600 "$CONTROL_APPROVAL"

# Canary first. Reset a failed unit when recovery needs it, but do not make an
# already healthy inactive unit depend on reset-failed succeeding. The recurring
# timer is enabled only after the exact merged SHA has deployed successfully and
# the public site has passed its health check.
if systemctl is-failed --quiet hermes-tech-pull-deploy.service; then
    sudo systemctl reset-failed hermes-tech-pull-deploy.service >/dev/null 2>&1
fi
sudo systemctl start hermes-tech-pull-deploy.service

[[ "$(systemctl show hermes-tech-pull-deploy.service -p Result --value)" == 'success' ]] \
    || fail 'pull deploy canary service did not finish successfully'
[[ "$(git -C "$PRIMARY" rev-parse HEAD)" == "$HEAD_SHA" ]] \
    || fail 'production did not reach the activated main SHA'
curl --fail --silent --show-error --max-time 20 https://tech.rozkalns.net/ >/dev/null

sudo systemctl enable --now hermes-tech-pull-deploy.timer
[[ "$(systemctl is-enabled hermes-tech-pull-deploy.timer)" == 'enabled' ]] \
    || fail 'pull deploy timer is not enabled after successful canary'
[[ "$(systemctl is-active hermes-tech-pull-deploy.timer)" == 'active' ]] \
    || fail 'pull deploy timer is not active after successful canary'

printf 'PULL_DEPLOY_ACTIVATION_RESULT=PASS\n'
printf 'SOURCE_SHA=%s\n' "$HEAD_SHA"
printf 'PRODUCTION_SHA=%s\n' "$(git -C "$PRIMARY" rev-parse HEAD)"
printf 'TIMER_ENABLED=%s\n' "$(systemctl is-enabled hermes-tech-pull-deploy.timer)"
printf 'TIMER_ACTIVE=%s\n' "$(systemctl is-active hermes-tech-pull-deploy.timer)"
printf 'PUBLIC_SITE=PASS\n'
printf 'FUTURE_GIT_EMAIL=noreply\n'
printf 'DATABASE_MIGRATIONS_EXECUTED=false\n'