#!/usr/bin/env bash
# Timezone-safe adapter around the byte-preserved publication implementation.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BASE="${HERMES_TECH_ROOT:-$HOME/hermes-tech}"
[[ "$BASE" == /* ]] || {
    echo "KĻŪDA: HERMES_TECH_ROOT jābūt absolūtam ceļam" >&2
    exit 2
}
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
CORE=$(resolve_runtime_file publish_core.sh)
ADAPTER=$(resolve_runtime_file tools/timezone_shell_adapter.py)
HERMES_TIME_PY=$(resolve_runtime_file hermes_time.py)
PENDING_PREFLIGHT=$(resolve_runtime_file digest_pending.py)
export HERMES_TIME_PY

# Hard gate before publish_core.sh can change Hugo content, public files, DB,
# Git index or remote main. Every pending draft must have unique selected IDs
# and no same-category topic_key reuse across dates.
"$PYTHON" "$PENDING_PREFLIGHT" validate \
    --root "$BASE" \
    --db "$BASE/data/hermes.db"

PATCHED=$("$PYTHON" "$ADAPTER" publish "$CORE") || exit $?
exec bash -c "$PATCHED" "$CORE" "$@"
