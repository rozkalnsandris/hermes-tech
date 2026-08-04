#!/usr/bin/env bash
# Fail-closed synchronization for Hermes Tech production-generated content commits.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly EXIT_USAGE=2
readonly EXIT_SYNC=76
readonly REMOTE="${HERMES_GIT_SYNC_REMOTE:-origin}"
readonly BRANCH="${HERMES_GIT_SYNC_BRANCH:-main}"
readonly NETWORK_TIMEOUT="${HERMES_GIT_SYNC_TIMEOUT_SECONDS:-20}"

usage() {
    cat >&2 <<'USAGE'
Usage:
  sync_generated_content.sh preflight <devops|ai|agents> <YYYY-MM-DD> <digest-path>
  sync_generated_content.sh verify-index <devops|ai|agents> <YYYY-MM-DD> <digest-path> <base-sha>
  sync_generated_content.sh verify-noop <devops|ai|agents> <YYYY-MM-DD> <digest-path> <base-sha>
  sync_generated_content.sh sync <devops|ai|agents> <YYYY-MM-DD> <digest-path> <base-sha> <commit-sha>
USAGE
    exit "$EXIT_USAGE"
}

fail() {
    printf 'KĻŪDA: HERMES_GIT_SYNC: %s\n' "$*" >&2
    exit "$EXIT_SYNC"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "nav atrasta komanda '$1'"
}

for command_name in git timeout; do
    require_command "$command_name"
done

[[ "$NETWORK_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || fail \
    "HERMES_GIT_SYNC_TIMEOUT_SECONDS jābūt pozitīvam veselam skaitlim"

[[ $# -ge 4 ]] || usage
mode=$1
category=$2
digest_date=$3
digest_path=$4
shift 4

[[ "$digest_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || fail \
    "nederīgs datums '$digest_date'"

case "$category" in
    devops)
        section="digest"
        [[ "$digest_path" == "digests/$digest_date-devops.md" || \
           "$digest_path" == "digests/$digest_date.md" ]] || fail \
            "DevOps digesta ceļš nav atļauts: $digest_path"
        ;;
    ai|agents)
        section="$category"
        [[ "$digest_path" == "digests/$digest_date-$category.md" ]] || fail \
            "$category digesta ceļš nav atļauts: $digest_path"
        ;;
    *)
        fail "nezināma kategorija '$category'"
        ;;
esac

content_path="site/content/$section/$digest_date.md"
og_path="site/static/og/$digest_date-$category.png"
readonly digest_path content_path og_path
readonly -a COMMIT_PATHS=("$digest_path" "$content_path" "$og_path")
readonly -a PENDING_DIGEST_PATHS=(
    "digests/$digest_date-devops.md"
    "digests/$digest_date.md"
    "digests/$digest_date-ai.md"
    "digests/$digest_date-agents.md"
)

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || fail \
    "komanda nav palaista Git repozitorijā"
cd "$repo_root"

current_branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)
[[ "$current_branch" == "$BRANCH" ]] || fail \
    "vajadzīgs zars '$BRANCH', pašreizējais ir '${current_branch:-detached}'"

git remote get-url "$REMOTE" >/dev/null 2>&1 || fail \
    "nav konfigurēts remote '$REMOTE'"

export GIT_TERMINAL_PROMPT=0

network_git() {
    timeout --signal=TERM --kill-after=5s "${NETWORK_TIMEOUT}s" git "$@"
}

fetch_remote() {
    network_git fetch --quiet --no-tags "$REMOTE" \
        "refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH" || fail \
        "neizdevās droši nolasīt $REMOTE/$BRANCH"
    git show-ref --verify --quiet "refs/remotes/$REMOTE/$BRANCH" || fail \
        "pēc fetch nav pieejams refs/remotes/$REMOTE/$BRANCH"
}

is_commit_path() {
    local candidate=$1
    local allowed
    for allowed in "${COMMIT_PATHS[@]}"; do
        [[ "$candidate" == "$allowed" ]] && return 0
    done
    return 1
}

is_pending_path() {
    local candidate=$1
    local allowed
    if is_commit_path "$candidate"; then
        return 0
    fi
    for allowed in "${PENDING_DIGEST_PATHS[@]}"; do
        [[ "$candidate" == "$allowed" ]] && return 0
    done
    return 1
}

collect_paths() {
    local kind=$1
    case "$kind" in
        staged)
            git diff --cached --name-only --diff-filter=ACDMRTUXB
            ;;
        unstaged)
            git diff --name-only --diff-filter=ACDMRTUXB
            ;;
        untracked)
            git ls-files --others --exclude-standard
            ;;
        *)
            fail "iekšēja kļūda: nezināms path tips '$kind'"
            ;;
    esac
}

assert_no_staged_changes() {
    local path
    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        fail "preflight laikā jau ir staged izmaiņa: $path"
    done < <(collect_paths staged)
}

assert_pending_only() {
    local kind path
    for kind in unstaged untracked; do
        while IFS= read -r path; do
            [[ -z "$path" ]] && continue
            is_pending_path "$path" || fail \
                "neatļauta $kind izmaiņa ārpus šīs dienas publicēšanas ceļiem: $path"
        done < <(collect_paths "$kind")
    done
}

assert_index_contract() {
    local staged_count=0
    local path kind

    while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        is_commit_path "$path" || fail \
            "staged ceļš nav atļauts šai publikācijai: $path"
        staged_count=$((staged_count + 1))
    done < <(collect_paths staged)

    (( staged_count > 0 )) || fail \
        "nav staged publikācijas izmaiņu"

    for kind in unstaged untracked; do
        while IFS= read -r path; do
            [[ -z "$path" ]] && continue
            is_pending_path "$path" || fail \
                "pēc staging palikusi neatļauta $kind izmaiņa: $path"
        done < <(collect_paths "$kind")
    done
}

relation_error() {
    local local_sha=$1
    local remote_sha=$2
    local state
    if git merge-base --is-ancestor "$remote_sha" "$local_sha"; then
        state="LOCAL_AHEAD"
    elif git merge-base --is-ancestor "$local_sha" "$remote_sha"; then
        state="REMOTE_AHEAD"
    else
        state="DIVERGED"
    fi
    fail "stāvoklis=$state local=$local_sha remote=$remote_sha; automātiska merge/rebase/force-push netiek veikta"
}

assert_base_equal_remote() {
    local expected_base=${1:-}
    local local_sha remote_sha
    [[ "$expected_base" =~ ^[0-9a-f]{40}$ ]] || fail \
        "nederīgs sagaidītais base SHA '$expected_base'"
    local_sha=$(git rev-parse HEAD)
    [[ "$local_sha" == "$expected_base" ]] || fail \
        "lokālais HEAD mainījās: expected=$expected_base actual=$local_sha"
    fetch_remote
    remote_sha=$(git rev-parse "refs/remotes/$REMOTE/$BRANCH")
    [[ "$remote_sha" == "$expected_base" ]] || relation_error \
        "$local_sha" "$remote_sha"
}

case "$mode" in
    preflight)
        [[ $# -eq 0 ]] || usage
        assert_no_staged_changes
        assert_pending_only
        fetch_remote
        local_sha=$(git rev-parse HEAD)
        remote_sha=$(git rev-parse "refs/remotes/$REMOTE/$BRANCH")
        [[ "$local_sha" == "$remote_sha" ]] || relation_error \
            "$local_sha" "$remote_sha"
        printf '%s\n' "$local_sha"
        ;;

    verify-index)
        [[ $# -eq 1 ]] || usage
        expected_base=$1
        assert_base_equal_remote "$expected_base"
        assert_index_contract
        ;;

    verify-noop)
        [[ $# -eq 1 ]] || usage
        expected_base=$1
        assert_no_staged_changes
        assert_pending_only
        assert_base_equal_remote "$expected_base"
        printf 'HERMES_GIT_SYNC_NOOP local=%s remote=%s\n' \
            "$expected_base" "$expected_base"
        ;;

    sync)
        [[ $# -eq 2 ]] || usage
        expected_base=$1
        expected_commit=$2
        [[ "$expected_base" =~ ^[0-9a-f]{40}$ ]] || fail \
            "nederīgs base SHA '$expected_base'"
        [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] || fail \
            "nederīgs commit SHA '$expected_commit'"

        local_sha=$(git rev-parse HEAD)
        [[ "$local_sha" == "$expected_commit" ]] || fail \
            "HEAD nav sagaidītais publication commits: expected=$expected_commit actual=$local_sha"

        parent_sha=$(git rev-parse "$expected_commit^")
        [[ "$parent_sha" == "$expected_base" ]] || fail \
            "publication commits nav tiešs preflight base pēctecis: parent=$parent_sha base=$expected_base"

        commit_subject=$(git show -s --format=%s "$expected_commit")
        [[ "$commit_subject" == "Publish $category digest $digest_date" ]] || fail \
            "neatļauts commit virsraksts: $commit_subject"

        changed_count=0
        while IFS= read -r path; do
            [[ -z "$path" ]] && continue
            is_commit_path "$path" || fail \
                "commits satur neatļautu ceļu: $path"
            changed_count=$((changed_count + 1))
        done < <(git diff-tree --no-commit-id --name-only -r "$expected_commit")
        (( changed_count > 0 )) || fail "publication commits ir tukšs"

        fetch_remote
        remote_sha=$(git rev-parse "refs/remotes/$REMOTE/$BRANCH")
        [[ "$remote_sha" == "$expected_base" ]] || relation_error \
            "$expected_commit" "$remote_sha"

        network_git push --porcelain "$REMOTE" \
            "$expected_commit:refs/heads/$BRANCH" >/dev/null || fail \
            "fast-forward push uz $REMOTE/$BRANCH neizdevās; lokālais commits $expected_commit ir saglabāts"

        fetch_remote
        remote_sha=$(git rev-parse "refs/remotes/$REMOTE/$BRANCH")
        local_sha=$(git rev-parse HEAD)
        [[ "$remote_sha" == "$expected_commit" ]] || fail \
            "pēc push remote SHA neatbilst: expected=$expected_commit remote=$remote_sha"
        [[ "$local_sha" == "$remote_sha" ]] || fail \
            "pēc push local/remote SHA neatbilst: local=$local_sha remote=$remote_sha"

        printf 'HERMES_GIT_SYNC_OK local=%s remote=%s\n' \
            "$local_sha" "$remote_sha"
        ;;

    *)
        usage
        ;;
esac
