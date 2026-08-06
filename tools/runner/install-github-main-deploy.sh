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
SOURCE_REL='tools/runner/release/hermes-tech-deploy-main'
SOURCE="$SOURCE_WORKTREE/$SOURCE_REL"
DEST='/usr/local/sbin/hermes-tech-deploy-main'
SUDOERS='/etc/sudoers.d/hermes-tech-github-deploy'
RUNNER='github-tech-runner'
OWNER='andris'
RUNNER_SERVICE='actions.runner.rozkalnsandris-hermes-tech.rpi5-hermes-tech-release.service'

for command_name in awk bash cat chmod git grep id install mktemp rm runuser sha256sum sudo systemctl tr visudo; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done

id "$RUNNER" >/dev/null 2>&1 || fail 'Tech runner user is missing'
id "$OWNER" >/dev/null 2>&1 || fail 'owner user is missing'
[[ "$(pwd -P)" == "$SOURCE_WORKTREE" ]] || fail 'installer must run from release-control worktree'
[[ -f "$SOURCE" && ! -L "$SOURCE" ]] || fail 'deploy helper source is missing or unsafe'

owner_git() {
    runuser -u "$OWNER" -- env \
        HOME=/home/andris \
        PATH='/home/andris/.local/bin:/usr/local/bin:/usr/bin:/bin' \
        git -C "$SOURCE_WORKTREE" "$@"
}

owner_git ls-files --error-unmatch "$SOURCE_REL" >/dev/null \
    || fail 'deploy helper source is not tracked'
HEAD_SHA=$(owner_git rev-parse HEAD)
REMOTE_SHA=$(owner_git rev-parse refs/remotes/origin/main)
[[ "$HEAD_SHA" == "$REMOTE_SHA" ]] || fail 'release-control is not exact origin/main'
[[ -z "$(owner_git branch --show-current)" ]] || fail 'release-control must remain detached'
[[ -z "$(owner_git status --porcelain=v1 --untracked-files=all)" ]] \
    || fail 'release-control worktree is not clean'

bash -n "$SOURCE"
systemctl is-active --quiet "$RUNNER_SERVICE" || fail 'GitHub Tech runner service is not active'
if id -nG "$RUNNER" | tr ' ' '\n' | grep -Fxq docker; then
    fail 'Tech runner must not belong to docker group'
fi

TMPDIR_INSTALL=$(mktemp -d /tmp/hermes-tech-github-deploy.XXXXXXXX)
cleanup() {
    rm -rf -- "$TMPDIR_INSTALL"
}
trap cleanup EXIT

cat >"$TMPDIR_INSTALL/sudoers" <<'SUDOERS'
Defaults!/usr/local/sbin/hermes-tech-deploy-main env_reset,secure_path=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
github-tech-runner ALL=(root) NOPASSWD: /usr/local/sbin/hermes-tech-deploy-main *
SUDOERS
chmod 0440 "$TMPDIR_INSTALL/sudoers"
visudo -cf "$TMPDIR_INSTALL/sudoers" >/dev/null

install -o root -g root -m 0755 "$SOURCE" "$DEST"
install -o root -g root -m 0440 "$TMPDIR_INSTALL/sudoers" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

sudo -n -l -U "$RUNNER" -- "$DEST" "$HEAD_SHA" \
    "/home/github-tech-runner/actions-runner/_work/_temp/hermes-tech-main-deploy-1-1" \
    >/dev/null 2>&1 || fail 'narrow deploy sudo rule is not visible to runner'

printf 'GITHUB_TECH_DEPLOY_INSTALL_RESULT=PASS\n'
printf 'SOURCE_SHA=%s\n' "$HEAD_SHA"
printf 'HELPER_SHA256=%s\n' "$(sha256sum "$DEST" | awk '{print $1}')"
printf 'RUNNER_SERVICE=%s\n' "$RUNNER_SERVICE"
printf 'RUNNER_HAS_DOCKER_GROUP=false\n'
printf 'DATABASE_MIGRATIONS_AUTHORIZED=false\n'
printf 'PRODUCTION_CHANGED=false\n'
