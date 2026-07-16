#!/usr/bin/env bash
# Hermes Tech collector cron runner.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BASE="$HOME/hermes-tech"
PYTHON="$BASE/venv/bin/python"
ENV_FILE="$BASE/.env"
LOCK="$BASE/.collector.lock"
HC_URL=""
HC_STARTED=0
HC_FINISHED=0

log() {
    printf '%s [collector-runner] %s\n' "$(date --iso-8601=seconds)" "$*"
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
        ping_healthcheck "$HC_URL" "/$rc"
        log "Mēģināts nosūtīt rezerves failure ping pēc neparedzētas iziešanas (rc=$rc)"
    fi
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

check_only() {
    [[ -x "$PYTHON" ]] || { log "KĻŪDA: nav izpildāms $PYTHON"; return 1; }
    [[ -f "$BASE/collector.py" ]] || { log "KĻŪDA: nav collector.py"; return 1; }
    "$PYTHON" -m py_compile "$BASE/collector.py"
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
    log "KĻŪDA: iepriekšējā collector palaišana vēl darbojas"
    exit 75
fi

HC_URL=$(read_env_value HEALTHCHECK_COLLECTOR_URL)
if [[ -n "$HC_URL" ]]; then
    ping_healthcheck "$HC_URL" "/start"
    HC_STARTED=1
fi

set +e
timeout --signal=TERM --kill-after=30s 25m \
    "$PYTHON" "$BASE/collector.py"
rc=$?
set -e

if (( rc == 0 )); then
    ping_healthcheck "$HC_URL"
    HC_FINISHED=1
    log "Pabeigts veiksmīgi"
else
    ping_healthcheck "$HC_URL" "/$rc"
    HC_FINISHED=1
    if (( rc == 124 )); then
        log "KĻŪDA: collector pārsniedza 25 minūšu limitu"
    else
        log "KĻŪDA: collector beidzās ar kodu $rc"
    fi
fi
exit "$rc"
