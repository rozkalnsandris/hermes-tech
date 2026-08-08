#!/usr/bin/env bash
# Timezone-safe adapter around the byte-preserved digest pipeline implementation.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BASE="${HERMES_TECH_ROOT:-$HOME/hermes-tech}"
[[ "$BASE" == /* ]] || {
    echo "KĻŪDA: HERMES_TECH_ROOT jābūt absolūtam ceļam" >&2
    exit 2
}
export HERMES_TECH_ROOT="$BASE"
PYTHON="$BASE/venv/bin/python"

resolve_runtime_file() {
    local relative=$1
    local primary="$BASE/$relative"
    if [[ -f "$primary" ]]; then
        printf '%s\n' "$primary"
        return 0
    fi

    # Integration tests copy only the public entrypoint into an isolated root.
    # A tracked source checkout may provide the immutable core/helper files.
    local source_root=${HERMES_TIME_SOURCE_ROOT:-$PWD}
    local fallback="$source_root/$relative"
    if [[ "$source_root" != "$BASE" && -d "$source_root/.git" && -f "$fallback" ]] \
        && git -C "$source_root" ls-files --error-unmatch "$relative" >/dev/null 2>&1; then
        printf '%s\n' "$fallback"
        return 0
    fi
    echo "KĻŪDA: nav runtime faila $primary" >&2
    return 1
}

[[ -x "$PYTHON" ]] || { echo "KĻŪDA: nav izpildāms $PYTHON" >&2; exit 1; }
CORE=$(resolve_runtime_file run_digests_core.sh)
ADAPTER=$(resolve_runtime_file tools/timezone_shell_adapter.py)
HERMES_TIME_PY=$(resolve_runtime_file hermes_time.py)
export HERMES_TIME_PY

PATCHED=$("$PYTHON" "$ADAPTER" digest-runner "$CORE") || exit $?

# Preserve the lightweight check path exactly and do not emit operational
# Telegram alerts for an explicitly requested local --check invocation.
if (( $# > 0 )); then
    exec bash -c "$PATCHED" "$CORE" "$@"
fi

# Full scheduled runs can fail before run_digests_core.sh reaches its normal
# Telegram summary block (for example, during global classification). Remember
# the current log boundary so a post-run notifier can inspect only this run.
LOG_FILE="$BASE/logs/digest-cron.log"
if [[ -f "$LOG_FILE" ]]; then
    LOG_START_SIZE=$(stat -c '%s' "$LOG_FILE" 2>/dev/null || printf '0')
else
    LOG_START_SIZE=0
fi

set +e
bash -c "$PATCHED" "$CORE"
rc=$?
set -e

if (( rc != 0 )); then
    if NOTIFIER=$(resolve_runtime_file tools/notify_pipeline_failure.py); then
        set +e
        "$PYTHON" "$NOTIFIER" "$BASE" "$LOG_FILE" "$LOG_START_SIZE" "$rc"
        notify_rc=$?
        set -e
        if (( notify_rc != 0 )); then
            echo "BRĪDINĀJUMS: agrīno Telegram kļūdas paziņojumu neizdevās nosūtīt (rc=$notify_rc)" >&2
        fi
    else
        echo "BRĪDINĀJUMS: nav agrīnā Telegram kļūdas notifiera" >&2
    fi
fi

exit "$rc"
