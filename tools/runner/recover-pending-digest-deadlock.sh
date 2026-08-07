#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

[[ ${EUID:-$(id -u)} -ne 0 ]] || fail 'run recovery as the andris user, not root'

PRIMARY='/home/andris/hermes-tech'
SOURCE='/home/andris/hermes-tech-worktrees/release-control'
DEPLOY_HELPER='/usr/local/sbin/hermes-tech-deploy-main'
INSTALLER_REL='tools/runner/install-github-main-deploy.sh'
RUNNER_TEMP='/home/github-tech-runner/actions-runner/_work/_temp'
PUBLIC_URL='https://tech.rozkalns.net/'

for command_name in awk bash cp curl date dirname flock git install mkdir mktemp rm sha256sum sudo; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done

[[ -d "$PRIMARY" && ! -L "$PRIMARY" ]] || fail 'production checkout is missing or unsafe'
[[ -d "$SOURCE" && ! -L "$SOURCE" ]] || fail 'release-control worktree is missing or unsafe'
[[ -x "$DEPLOY_HELPER" ]] || fail 'installed deploy helper is missing'
[[ "$(git -C "$PRIMARY" branch --show-current)" == 'main' ]] || fail 'production checkout must be on main'
[[ -z "$(git -C "$SOURCE" branch --show-current)" ]] || fail 'release-control must remain detached'
[[ -z "$(git -C "$SOURCE" status --porcelain=v1 --untracked-files=all)" ]] || fail 'release-control is not clean'

exec 8>"$PRIMARY/.digest-pipeline.lock"
flock -n 8 || fail 'digest pipeline is active; recovery will not run concurrently'

printf 'CHECK: fetching exact origin/main\n'
git -C "$PRIMARY" fetch --prune origin main
git -C "$SOURCE" fetch --prune origin main
LOCAL_SHA=$(git -C "$PRIMARY" rev-parse HEAD)
REMOTE_SHA=$(git -C "$PRIMARY" rev-parse refs/remotes/origin/main)
[[ "$LOCAL_SHA" != "$REMOTE_SHA" ]] || fail \
    'production is not behind origin/main; this recovery only handles REMOTE_AHEAD'
git -C "$PRIMARY" merge-base --is-ancestor "$LOCAL_SHA" "$REMOTE_SHA" || fail \
    'production and origin/main are not a simple REMOTE_AHEAD fast-forward state'

PENDING_DATE=''
declare -a PENDING_PATHS=()
declare -A PENDING_SHA256=()
declare -A CATEGORY_PATH=()

while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    status=${line:0:2}
    path=${line:3}
    [[ "$status" == '??' ]] || fail "unexpected tracked/staged production change: $line"

    category=''
    digest_date=''
    if [[ "$path" =~ ^digests/([0-9]{4}-[0-9]{2}-[0-9]{2})-(devops|ai|agents)\.md$ ]]; then
        digest_date=${BASH_REMATCH[1]}
        category=${BASH_REMATCH[2]}
    elif [[ "$path" =~ ^digests/([0-9]{4}-[0-9]{2}-[0-9]{2})\.md$ ]]; then
        digest_date=${BASH_REMATCH[1]}
        category='devops'
    else
        fail "unexpected untracked production path: $path"
    fi

    [[ -z "$PENDING_DATE" || "$PENDING_DATE" == "$digest_date" ]] || fail \
        "pending digests span multiple dates: $PENDING_DATE and $digest_date"
    [[ ${CATEGORY_PATH[$category]+present} != present ]] || fail \
        "more than one pending digest maps to category $category"
    [[ -f "$PRIMARY/$path" && ! -L "$PRIMARY/$path" ]] || fail \
        "pending digest is not a regular file: $path"
    if git -C "$PRIMARY" cat-file -e "${REMOTE_SHA}:${path}" 2>/dev/null; then
        fail "origin/main already tracks pending digest path: $path"
    fi

    PENDING_DATE=$digest_date
    PENDING_PATHS+=("$path")
    CATEGORY_PATH["$category"]=$path
    PENDING_SHA256["$path"]=$(sha256sum "$PRIMARY/$path" | awk '{print $1}')
done < <(git -C "$PRIMARY" status --porcelain=v1 --untracked-files=all)

(( ${#PENDING_PATHS[@]} > 0 )) || fail 'no pending generated digest source files found'
(( ${#PENDING_PATHS[@]} <= 3 )) || fail 'unexpected number of pending generated digest source files'

BACKUP_DIR=$(mktemp -d "$HOME/.hermes-tech-digest-recovery.XXXXXXXX")
SUCCESS=0

cleanup() {
    local rc=$?
    trap - EXIT
    if (( SUCCESS == 0 )); then
        if [[ -d "$BACKUP_DIR" ]]; then
            for path in "${PENDING_PATHS[@]}"; do
                if [[ -f "$BACKUP_DIR/$path" && ! -e "$PRIMARY/$path" ]]; then
                    mkdir -p "$(dirname "$PRIMARY/$path")"
                    cp -p -- "$BACKUP_DIR/$path" "$PRIMARY/$path" || true
                fi
            done
            printf 'RECOVERY_BACKUP_PRESERVED=%s\n' "$BACKUP_DIR" >&2
        fi
    else
        rm -rf -- "$BACKUP_DIR"
    fi
    exit "$rc"
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR/digests"
for path in "${PENDING_PATHS[@]}"; do
    cp -p -- "$PRIMARY/$path" "$BACKUP_DIR/$path"
    [[ "$(sha256sum "$BACKUP_DIR/$path" | awk '{print $1}')" == "${PENDING_SHA256[$path]}" ]] || fail \
        "backup checksum mismatch for $path"
done

printf 'CHECK: protecting pending digests and making production temporarily clean\n'
exec 9>"$PRIMARY/.publish.lock"
flock -w 30 9 || fail 'could not acquire publication lock'
for path in "${PENDING_PATHS[@]}"; do
    rm -- "$PRIMARY/$path"
done
[[ -z "$(git -C "$PRIMARY" status --porcelain=v1 --untracked-files=all)" ]] || fail \
    'production did not become clean after protecting pending digests'
flock -u 9

EVIDENCE_DIR="$RUNNER_TEMP/hermes-tech-main-deploy-$(date +%s)-$$"
sudo install -d -o github-tech-runner -g github-tech-runner -m 0700 "$EVIDENCE_DIR"

printf 'RECOVER: fast-forwarding production through installed guarded deploy helper\n'
sudo --non-interactive "$DEPLOY_HELPER" "$REMOTE_SHA" "$EVIDENCE_DIR"
[[ "$(git -C "$PRIMARY" rev-parse HEAD)" == "$REMOTE_SHA" ]] || fail \
    'production did not reach exact origin/main after deploy helper'

printf 'RECOVER: installing the reviewed deploy helper from exact origin/main\n'
[[ "$(git -C "$SOURCE" rev-parse HEAD)" == "$REMOTE_SHA" ]] || fail \
    'release-control did not land on the deployed origin/main SHA'
(
    cd "$SOURCE"
    sudo bash "$INSTALLER_REL"
)

printf 'RECOVER: restoring pending digest bytes\n'
flock -w 30 9 || fail 'could not reacquire publication lock'
[[ -z "$(git -C "$PRIMARY" status --porcelain=v1 --untracked-files=all)" ]] || fail \
    'production became dirty before pending digests were restored'
for path in "${PENDING_PATHS[@]}"; do
    [[ ! -e "$PRIMARY/$path" ]] || fail "refusing to overwrite path while restoring: $path"
    cp -p -- "$BACKUP_DIR/$path" "$PRIMARY/$path"
    [[ "$(sha256sum "$PRIMARY/$path" | awk '{print $1}')" == "${PENDING_SHA256[$path]}" ]] || fail \
        "restored checksum mismatch for $path"
done
flock -u 9

printf 'RECOVER: validating already-generated digests\n'
HERMES_TECH_ROOT="$PRIMARY" "$PRIMARY/venv/bin/python" "$PRIMARY/digest.py" validate

RECOVERED_CATEGORIES=''
for category in devops ai agents; do
    [[ ${CATEGORY_PATH[$category]+present} == present ]] || continue
    printf 'RECOVER: publishing %s digest for %s\n' "$category" "$PENDING_DATE"
    HERMES_TECH_ROOT="$PRIMARY" \
        "$PRIMARY/venv/bin/python" "$PRIMARY/digest.py" publish "$category" "$PENDING_DATE"
    if [[ -z "$RECOVERED_CATEGORIES" ]]; then
        RECOVERED_CATEGORIES=$category
    else
        RECOVERED_CATEGORIES="$RECOVERED_CATEGORIES,$category"
    fi
done

[[ -z "$(git -C "$PRIMARY" status --porcelain=v1 --untracked-files=all)" ]] || fail \
    'production checkout is not clean after recovered publication'
git -C "$PRIMARY" fetch --prune origin main
[[ "$(git -C "$PRIMARY" rev-parse HEAD)" == "$(git -C "$PRIMARY" rev-parse refs/remotes/origin/main)" ]] || fail \
    'production and origin/main differ after recovered publication'
curl --fail --silent --show-error --max-time 20 "$PUBLIC_URL" >/dev/null

SUCCESS=1
printf 'DEPLOY_DEADLOCK_RECOVERY_RESULT=PASS\n'
printf 'RECOVERED_DATE=%s\n' "$PENDING_DATE"
printf 'RECOVERED_CATEGORIES=%s\n' "$RECOVERED_CATEGORIES"
printf 'PRE_RECOVERY_PRODUCTION_SHA=%s\n' "$LOCAL_SHA"
printf 'RECOVERY_BASE_MAIN_SHA=%s\n' "$REMOTE_SHA"
printf 'DEPLOY_EVIDENCE_DIR=%s\n' "$EVIDENCE_DIR"
printf 'DATABASE_MIGRATIONS_EXECUTED=false\n'
printf 'DEPENDENCY_INSTALL_EXECUTED=false\n'
printf 'FORCE_PUSH_EXECUTED=false\n'