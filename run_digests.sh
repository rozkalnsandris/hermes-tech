#!/usr/bin/env bash
# Hermes Tech visu trīs rīta digestu cron runner.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BASE="$HOME/hermes-tech"
PYTHON="$BASE/venv/bin/python"
ENV_FILE="$BASE/.env"
LOCK="$BASE/.digest-pipeline.lock"
SLEEP_BETWEEN=240
HC_URL=""
HC_STARTED=0
HC_FINISHED=0

log() {
    printf '%s [digest-runner] %s\n' "$(date --iso-8601=seconds)" "$*"
}

read_env_value() {
    "$PYTHON" - "$ENV_FILE" "$1" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
if not path.exists():
    raise SystemExit(0)
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() == key:
        print(value.strip().strip('"').strip("'"))
        break
PY
}

ping_healthcheck() {
    local url=${1:-}
    local suffix=${2:-}
    [[ -n "$url" ]] || return 0
    url=${url%/}
    curl --fail --silent --show-error \
        --max-time 15 --retry 2 --retry-all-errors \
        "$url$suffix" >/dev/null \
        || log "BRĪDINĀJUMS: healthcheck ping '$suffix' neizdevās"
}

on_exit() {
    local rc=$?
    trap - EXIT
    if (( rc != 0 && HC_STARTED == 1 && HC_FINISHED == 0 )); then
        ping_healthcheck "$HC_URL" "/fail"
        log "Mēģināts nosūtīt rezerves failure ping pēc neparedzētas iziešanas (rc=$rc)"
    fi
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

check_only() {
    [[ -x "$PYTHON" ]] || { log "KĻŪDA: nav izpildāms $PYTHON"; return 1; }
    "$PYTHON" -m py_compile "$BASE/digest.py"
    bash -n "$BASE/publish.sh"
    command -v flock >/dev/null
    command -v timeout >/dev/null
    command -v curl >/dev/null
    log "Konfigurācijas pārbaude OK"
}

if [[ "${1:-}" == "--check" ]]; then
    check_only
    exit 0
fi

[[ $# -eq 0 ]] || { echo "Lietošana: $0 [--check]" >&2; exit 2; }
check_only

exec 9>"$LOCK"
if ! flock -n 9; then
    log "KĻŪDA: iepriekšējais digest pipeline vēl darbojas"
    exit 75
fi

HC_URL=$(read_env_value HEALTHCHECK_URL)
if [[ -n "$HC_URL" ]]; then
    ping_healthcheck "$HC_URL" "/start"
    HC_STARTED=1
fi

overall_rc=0
categories=(devops ai agents)
last_index=$((${#categories[@]} - 1))

for index in "${!categories[@]}"; do
    cat=${categories[$index]}
    log "Sāku kategoriju: $cat"

    set +e
    timeout --signal=TERM --kill-after=30s 10m \
        "$PYTHON" "$BASE/digest.py" "$cat"
    rc=$?
    set -e

    if (( rc == 0 )); then
        log "Kategorija $cat pabeigta veiksmīgi"
    else
        overall_rc=1
        if (( rc == 124 )); then
            log "KĻŪDA: $cat pārsniedza 10 minūšu limitu"
        else
            log "KĻŪDA: $cat beidzās ar kodu $rc"
        fi
    fi

    if (( index < last_index )); then
        log "Pauze ${SLEEP_BETWEEN}s līdz nākamajai kategorijai"
        sleep "$SLEEP_BETWEEN"
    fi
done

if (( overall_rc == 0 )); then
    ping_healthcheck "$HC_URL"
    HC_FINISHED=1
    log "Visi trīs digesti pabeigti veiksmīgi"
else
    ping_healthcheck "$HC_URL" "/fail"
    HC_FINISHED=1
    log "KĻŪDA: vismaz viens digests neizdevās"
fi
exit "$overall_rc"
