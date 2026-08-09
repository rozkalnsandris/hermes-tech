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
SOURCE_RUNTIME_HELPER_REL='tools/pull-deploy/release/hermes-tech-runtime-rollout'
SOURCE_RUNTIME_LAUNCHER_REL='tools/pull-deploy/runtime-rollout.sh'
SOURCE_POLLER_REL='tools/pull-deploy/release/hermes-tech-pull-deploy'
SOURCE_CLASSIFIER_REL='tools/classify_deploy_impact.py'
SOURCE_READINESS_REL='tools/pull-deploy/deploy_readiness.py'
SOURCE_SERVICE_REL='ops/systemd/hermes-tech-pull-deploy.service'
SOURCE_TIMER_REL='ops/systemd/hermes-tech-pull-deploy.timer'
DEST_HELPER='/usr/local/sbin/hermes-tech-deploy-main'
DEST_RUNTIME_HELPER='/usr/local/sbin/hermes-tech-runtime-rollout'
DEST_POLLER='/usr/local/sbin/hermes-tech-pull-deploy'
DEST_LIBEXEC='/usr/local/libexec/hermes-tech'
DEST_CLASSIFIER="$DEST_LIBEXEC/classify-deploy-impact"
DEST_READINESS="$DEST_LIBEXEC/deploy-readiness"
DEST_SERVICE='/etc/systemd/system/hermes-tech-pull-deploy.service'
DEST_TIMER='/etc/systemd/system/hermes-tech-pull-deploy.timer'
SUDOERS='/etc/sudoers.d/hermes-tech-pull-deploy'
STATE_ROOT='/home/andris/.local/state/hermes-tech-main-deploy'
EVIDENCE_ROOT="$STATE_ROOT/evidence"
INSTALLED_CONTROL_PLANE="$STATE_ROOT/installed-control-plane-sha"

for command_name in bash chmod chown gh git id install mktemp python3 rm runuser sha256sum sudo systemctl visudo; do
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
[[ -z "$(owner_git branch --show-current)" ]] || fail 'release-control worktree must remain detached'
[[ -z "$(owner_git status --porcelain=v1 --untracked-files=all)" ]] || fail 'release-control worktree is not clean'

for relative in \
    "$SOURCE_HELPER_REL" \
    "$SOURCE_RUNTIME_HELPER_REL" \
    "$SOURCE_RUNTIME_LAUNCHER_REL" \
    "$SOURCE_POLLER_REL" \
    "$SOURCE_CLASSIFIER_REL" \
    "$SOURCE_READINESS_REL" \
    "$SOURCE_SERVICE_REL" \
    "$SOURCE_TIMER_REL"; do
    owner_git ls-files --error-unmatch "$relative" >/dev/null || fail "source is not tracked: $relative"
    [[ -f "$SOURCE_WORKTREE/$relative" && ! -L "$SOURCE_WORKTREE/$relative" ]] || fail "source is missing or unsafe: $relative"
done
bash -n "$SOURCE_WORKTREE/$SOURCE_HELPER_REL"
bash -n "$SOURCE_WORKTREE/$SOURCE_RUNTIME_HELPER_REL"
bash -n "$SOURCE_WORKTREE/$SOURCE_RUNTIME_LAUNCHER_REL"
bash -n "$SOURCE_WORKTREE/$SOURCE_POLLER_REL"
python3 "$SOURCE_WORKTREE/$SOURCE_CLASSIFIER_REL" --help >/dev/null
python3 "$SOURCE_WORKTREE/$SOURCE_READINESS_REL" --help >/dev/null
runuser -u "$OWNER" -- env HOME="$OWNER_HOME" GH_CONFIG_DIR="$OWNER_HOME/.config/gh" \
    gh auth status --hostname github.com >/dev/null 2>&1 \
    || fail 'andris GitHub CLI authentication is unavailable'

install -d -o "$OWNER" -g "$OWNER" -m 0700 "$STATE_ROOT" "$EVIDENCE_ROOT"
install -d -o root -g root -m 0755 "$DEST_LIBEXEC"

TMPDIR_INSTALL=$(mktemp -d /tmp/hermes-tech-pull-deploy-install.XXXXXXXX)
cleanup() {
    rm -rf -- "$TMPDIR_INSTALL"
}
trap cleanup EXIT

cat >"$TMPDIR_INSTALL/sudoers" <<'SUDOERS'
Defaults!/usr/local/sbin/hermes-tech-deploy-main env_reset,secure_path=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Defaults!/usr/local/sbin/hermes-tech-runtime-rollout env_reset,secure_path=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
andris ALL=(root) NOPASSWD: /usr/local/sbin/hermes-tech-deploy-main *
andris ALL=(root) NOPASSWD: /usr/local/sbin/hermes-tech-runtime-rollout *
SUDOERS
chmod 0440 "$TMPDIR_INSTALL/sudoers"
visudo -cf "$TMPDIR_INSTALL/sudoers" >/dev/null

install -o root -g root -m 0755 "$SOURCE_WORKTREE/$SOURCE_HELPER_REL" "$DEST_HELPER"
install -o root -g root -m 0755 "$SOURCE_WORKTREE/$SOURCE_RUNTIME_HELPER_REL" "$DEST_RUNTIME_HELPER"
install -o root -g root -m 0755 "$SOURCE_WORKTREE/$SOURCE_POLLER_REL" "$DEST_POLLER"
install -o root -g root -m 0755 "$SOURCE_WORKTREE/$SOURCE_CLASSIFIER_REL" "$DEST_CLASSIFIER"
install -o root -g root -m 0755 "$SOURCE_WORKTREE/$SOURCE_READINESS_REL" "$DEST_READINESS"
install -o root -g root -m 0644 "$SOURCE_WORKTREE/$SOURCE_SERVICE_REL" "$DEST_SERVICE"
install -o root -g root -m 0644 "$SOURCE_WORKTREE/$SOURCE_TIMER_REL" "$DEST_TIMER"
install -o root -g root -m 0440 "$TMPDIR_INSTALL/sudoers" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

printf '%s\n' "$HEAD_SHA" >"$TMPDIR_INSTALL/installed-control-plane-sha"
install -o "$OWNER" -g "$OWNER" -m 0600 \
    "$TMPDIR_INSTALL/installed-control-plane-sha" "$INSTALLED_CONTROL_PLANE"

systemctl daemon-reload

sudo -n -l -U "$OWNER" -- "$DEST_HELPER" "$HEAD_SHA" \
    "$EVIDENCE_ROOT/deploy-$HEAD_SHA-19700101T000000Z-1" >/dev/null 2>&1 \
    || fail 'narrow deploy sudo rule is not visible to owner'
sudo -n -l -U "$OWNER" -- "$DEST_RUNTIME_HELPER" "$HEAD_SHA" \
    "$EVIDENCE_ROOT/runtime-$HEAD_SHA-19700101T000000Z-1" >/dev/null 2>&1 \
    || fail 'narrow runtime-rollout sudo rule is not visible to owner'

printf 'PULL_DEPLOY_INSTALL_RESULT=PASS\n'
printf 'SOURCE_SHA=%s\n' "$HEAD_SHA"
printf 'HELPER_SHA256=%s\n' "$(sha256sum "$DEST_HELPER" | awk '{print $1}')"
printf 'RUNTIME_ROLLOUT_SHA256=%s\n' "$(sha256sum "$DEST_RUNTIME_HELPER" | awk '{print $1}')"
printf 'POLLER_SHA256=%s\n' "$(sha256sum "$DEST_POLLER" | awk '{print $1}')"
printf 'CLASSIFIER_SHA256=%s\n' "$(sha256sum "$DEST_CLASSIFIER" | awk '{print $1}')"
printf 'READINESS_SHA256=%s\n' "$(sha256sum "$DEST_READINESS" | awk '{print $1}')"
printf 'INSTALLED_CONTROL_PLANE_SHA=%s\n' "$HEAD_SHA"
printf 'TIMER_ENABLED=%s\n' "$(systemctl is-enabled hermes-tech-pull-deploy.timer 2>/dev/null || true)"
printf 'PRODUCTION_CHANGED=false\n'
printf 'DATABASE_MIGRATIONS_AUTHORIZED=false\n'