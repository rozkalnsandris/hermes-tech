#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
export PATH

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run installer with sudo'

SOURCE_WORKTREE='/home/andris/hermes-tech-worktrees/release-control'
OWNER='andris'
OWNER_HOME='/home/andris'
SOURCE_HELPER_REL='tools/pull-deploy/release/hermes-tech-deploy-main'
SOURCE_POLLER_REL='tools/pull-deploy/release/hermes-tech-pull-deploy'
SOURCE_SERVICE_REL='ops/systemd/hermes-tech-pull-deploy.service'
SOURCE_TIMER_REL='ops/systemd/hermes-tech-pull-deploy.timer'
DEST_HELPER='/usr/local/sbin/hermes-tech-deploy-main'
DEST_POLLER='/usr/local/sbin/hermes-tech-pull-deploy'
DEST_SERVICE='/etc/systemd/system/hermes-tech-pull-deploy.service'
DEST_TIMER='/etc/systemd/system/hermes-tech-pull-deploy.timer'
SUDOERS='/etc/sudoers.d/hermes-tech-pull-deploy'
STATE_ROOT='/home/andris/.local/state/hermes-tech-main-deploy'
EVIDENCE_ROOT="$STATE_ROOT/evidence"

for command_name in bash chmod chown gh git id install mktemp rm runuser sha256sum sudo systemctl visudo; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done
id "$OWNER" >/dev/null 2>&1 || fail 'owner user is missing'
[[ "$(pwd -P)" == "$SOURCE_WORKTREE" ]] || fail 'installer must run from release-control worktree'

owner_git() {
    runuser -u "$OWNER" -- env \
        HOME="$OWNER_HOME" \
        PATH='/home/andris/.local/bin:/usr/local/bin:/usr/bin:/bin' \
        git -C "$SOURCE_WORKTREE" "$@"
}

owner_git fetch --prune origin main
HEAD_SHA=$(owner_git rev-parse HEAD)
REMOTE_SHA=$(owner_git rev-parse refs/remotes/origin/main)
[[ "$HEAD_SHA" == "$REMOTE_SHA" ]] || fail 'release-control is not exact origin/main'
[[ -z "$(owner_git branch --show-current)" ]] || fail 'release-control must remain detached'
[[ -z "$(owner_git status --porcelain=v1 --untracked-files=all)" ]] || fail 'release-control worktree is not clean'

for relative in "$SOURCE_HELPER_REL" "$SOURCE_POLLER_REL" "$SOURCE_SERVICE_REL" "$SOURCE_TIMER_REL"; do
    owner_git ls-files --error-unmatch "$relative" >/dev/null || fail "source is not tracked: $relative"
    [[ -f "$SOURCE_WORKTREE/$relative" && ! -L "$SOURCE_WORKTREE/$relative" ]] || fail "source is missing or unsafe: $relative"
done
bash -n "$SOURCE_WORKTREE/$SOURCE_HELPER_REL"
bash -n "$SOURCE_WORKTREE/$SOURCE_POLLER_REL"
runuser -u "$OWNER" -- env HOME="$OWNER_HOME" GH_CONFIG_DIR="$OWNER_HOME/.config/gh" \
    gh auth status --hostname github.com >/dev/null 2>&1 \
    || fail 'andris GitHub CLI authentication is unavailable'

install -d -o "$OWNER" -g "$OWNER" -m 0700 "$STATE_ROOT" "$EVIDENCE_ROOT"

TMPDIR_INSTALL=$(mktemp -d /tmp/hermes-tech-pull-deploy-install.XXXXXXXX)
cleanup() {
    rm -rf -- "$TMPDIR_INSTALL"
}
trap cleanup EXIT

cat >"$TMPDIR_INSTALL/sudoers" <<'SUDOERS'
Defaults!/usr/local/sbin/hermes-tech-deploy-main env_reset,secure_path=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
andris ALL=(root) NOPASSWD: /usr/local/sbin/hermes-tech-deploy-main *
SUDOERS
chmod 0440 "$TMPDIR_INSTALL/sudoers"
visudo -cf "$TMPDIR_INSTALL/sudoers" >/dev/null

install -o root -g root -m 0755 "$SOURCE_WORKTREE/$SOURCE_HELPER_REL" "$DEST_HELPER"
install -o root -g root -m 0755 "$SOURCE_WORKTREE/$SOURCE_POLLER_REL" "$DEST_POLLER"
install -o root -g root -m 0644 "$SOURCE_WORKTREE/$SOURCE_SERVICE_REL" "$DEST_SERVICE"
install -o root -g root -m 0644 "$SOURCE_WORKTREE/$SOURCE_TIMER_REL" "$DEST_TIMER"
install -o root -g root -m 0440 "$TMPDIR_INSTALL/sudoers" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null
systemctl daemon-reload

sudo -n -l -U "$OWNER" -- "$DEST_HELPER" "$HEAD_SHA" \
    "$EVIDENCE_ROOT/deploy-$HEAD_SHA-19700101T000000Z-1" >/dev/null 2>&1 \
    || fail 'narrow deploy sudo rule is not visible to owner'

printf 'PULL_DEPLOY_INSTALL_RESULT=PASS\n'
printf 'SOURCE_SHA=%s\n' "$HEAD_SHA"
printf 'HELPER_SHA256=%s\n' "$(sha256sum "$DEST_HELPER" | awk '{print $1}')"
printf 'POLLER_SHA256=%s\n' "$(sha256sum "$DEST_POLLER" | awk '{print $1}')"
printf 'TIMER_ENABLED=%s\n' "$(systemctl is-enabled hermes-tech-pull-deploy.timer 2>/dev/null || true)"
printf 'PRODUCTION_CHANGED=false\n'
printf 'DATABASE_MIGRATIONS_AUTHORIZED=false\n'
