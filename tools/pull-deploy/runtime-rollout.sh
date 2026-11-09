#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail 'run the runtime rollout launcher as andris, not root'
[[ $# -eq 1 || $# -eq 2 ]] || fail 'usage: runtime-rollout.sh <40-sha> [--preflight-only]'

TARGET_SHA=$1
MODE=${2:-apply}
[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || fail 'target SHA must be 40 lowercase hex characters'
[[ "$MODE" == apply || "$MODE" == --preflight-only ]] || fail 'second argument must be --preflight-only when present'

SOURCE='/home/andris/hermes-tech-worktrees/release-control'
PRIMARY='/home/andris/hermes-tech'
STATE_ROOT='/home/andris/.local/state/hermes-tech-main-deploy'
EVIDENCE_ROOT="$STATE_ROOT/evidence"
HELPER='/usr/local/sbin/hermes-tech-runtime-rollout'

for command_name in date git install sudo; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done
[[ "$(pwd -P)" == "$SOURCE" ]] || fail 'runtime rollout must start from the release-control worktree'
[[ -x "$HELPER" && ! -L "$HELPER" ]] || fail 'installed runtime rollout helper is missing; activate the R3 control plane first'

git fetch --prune origin main
REMOTE_SHA=$(git rev-parse refs/remotes/origin/main)
[[ "$REMOTE_SHA" == "$TARGET_SHA" ]] || fail 'requested target is not exact origin/main'
[[ -z "$(git branch --show-current)" ]] || fail 'release-control worktree must remain detached'
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || fail 'release-control worktree is not clean'
git checkout --detach "$TARGET_SHA"
[[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]] || fail 'release-control HEAD mismatch'
[[ "$(git -C "$PRIMARY" branch --show-current)" == main ]] || fail 'production checkout must remain on main'

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_DIR="$EVIDENCE_ROOT/runtime-$TARGET_SHA-$STAMP-$$"
install -d -m 0700 "$STATE_ROOT" "$EVIDENCE_ROOT" "$EVIDENCE_DIR"

printf 'RUNTIME_ROLLOUT_REQUESTED_SHA=%s\n' "$TARGET_SHA"
printf 'RUNTIME_ROLLOUT_EVIDENCE=%s\n' "$EVIDENCE_DIR"
printf 'RUNTIME_ROLLOUT_MODE=%s\n' "$MODE"

if [[ "$MODE" == --preflight-only ]]; then
    sudo --non-interactive "$HELPER" "$TARGET_SHA" "$EVIDENCE_DIR" --preflight-only
else
    sudo --non-interactive "$HELPER" "$TARGET_SHA" "$EVIDENCE_DIR"
fi
