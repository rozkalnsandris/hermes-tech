#!/usr/bin/env bash
# Hermes Tech v4 digest pipeline — ar globalo router + cross-category validation.
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
        log "Mēģināts nosūtīt rezerves failure ping pēc iziešanas (rc=$rc)"
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

# HERMES_PARTIAL_PUBLISH_V1
overall_rc=0
categories=(devops ai agents)
successful_categories=()
failed_generation=()
published_categories=()
failed_publish=()

# ============================================================
# PHASE 1: Global event router (classify)
# ============================================================
log "=== PHASE 1: GLOBAL EVENT ROUTER ==="
set +e
timeout --signal=TERM --kill-after=30s 5m \
    "$PYTHON" "$BASE/digest.py" classify
rc=$?
set -e
if (( rc != 0 )); then
    log "KĻŪDA: globalā klasifikācija neizdevās (rc=$rc)"
    ping_healthcheck "$HC_URL" "/fail"
    HC_FINISHED=1
    exit 1
fi
log "Fāze 1 pabeigta"

# ============================================================
# PHASE 2: Generate each category independently
# ============================================================
log "=== PHASE 2: GENERATE DIGESTS ==="
for index in "${!categories[@]}"; do
    cat=${categories[$index]}
    log "Sāku kategoriju: $cat"

    set +e
    timeout --signal=TERM --kill-after=30s 10m \
        "$PYTHON" "$BASE/digest.py" digest "$cat"
    rc=$?
    set -e

    if (( rc == 0 )); then
        successful_categories+=("$cat")
        log "Kategorija $cat ģenerēta veiksmīgi"
    else
        overall_rc=1
        failed_generation+=("$cat")
        if (( rc == 124 )); then
            log "KĻŪDA: $cat pārsniedza 10 minūšu limitu"
        else
            log "KĻŪDA: $cat ģenerēšana beidzās ar kodu $rc"
        fi
    fi

    if (( index < ${#categories[@]} - 1 )); then
        log "Pauze ${SLEEP_BETWEEN}s līdz nākamajai kategorijai"
        sleep "$SLEEP_BETWEEN"
    fi
done

if (( ${#successful_categories[@]} == 0 )); then
    log "KĻŪDA: neviena kategorija netika veiksmīgi ģenerēta — nav ko publicēt"
    ping_healthcheck "$HC_URL" "/fail"
    HC_FINISHED=1
    exit 1
fi

if (( ${#failed_generation[@]} > 0 )); then
    log "BRĪDINĀJUMS: ${#failed_generation[@]} kategorija(s) neizdevās"
    log "Turpinu ar ${#successful_categories[@]} veiksmīgi ģenerēto kategoriju publicēšanu"
fi

# ============================================================
# PHASE 3: Global cross-category integrity validation
# Hard gate: routing conflict still blocks every publication.
# ============================================================
log "=== PHASE 3: CROSS-CATEGORY VALIDATION ==="
set +e
"$PYTHON" "$BASE/digest.py" validate
rc=$?
set -e

if (( rc != 0 )); then
    log "KĻŪDA: cross-category validācija neizdevās — publicēšana atcelta"
    ping_healthcheck "$HC_URL" "/fail"
    HC_FINISHED=1
    exit 1
fi
log "Cross-category validācija OK"

# ============================================================
# PHASE 4: Publish only categories generated successfully now
# publish.sh itself remains the per-category atomic/rollback boundary.
# ============================================================
log "=== PHASE 4: PUBLISH SUCCESSFUL CATEGORIES ==="
TODAY=$(TZ=UTC date +%Y-%m-%d)
publish_failures=0
published_count=0

for cat in "${successful_categories[@]}"; do
    log "Publicēju: $cat"
    set +e
    "$PYTHON" "$BASE/digest.py" publish "$cat" "$TODAY"
    rc=$?
    set -e

    if (( rc == 0 )); then
        published_count=$((published_count + 1))
        published_categories+=("$cat")
        log "$cat publicēts OK"
    else
        overall_rc=1
        publish_failures=$((publish_failures + 1))
        failed_publish+=("$cat")
        log "KĻŪDA: $cat publicēšana neizdevās (rc=$rc)"
    fi
done

if (( overall_rc == 0 )); then
    ping_healthcheck "$HC_URL"
    HC_FINISHED=1
else
    ping_healthcheck "$HC_URL" "/fail"
    HC_FINISHED=1
fi

# BEGIN MANAGED: TELEGRAM_PIPELINE_SUMMARY_V2
# Send one summary after every completed full/partial pipeline run.
join_csv() {
    local IFS=,
    printf '%s' "$*"
}

published_csv="$(join_csv "${published_categories[@]}")"
failed_generation_csv="$(join_csv "${failed_generation[@]}")"
failed_publish_csv="$(join_csv "${failed_publish[@]}")"

set +e
"$PYTHON" - \
    "$BASE/digest.py" \
    "$BASE/.env" \
    "$BASE/logs/digest-cron.log" \
    "$TODAY" \
    "$overall_rc" \
    "$published_csv" \
    "$failed_generation_csv" \
    "$failed_publish_csv" <<'PY_TELEGRAM_SUMMARY'
from pathlib import Path
import importlib.util
import re
import sys

(
    digest_raw,
    env_raw,
    log_raw,
    today,
    overall_rc_raw,
    published_raw,
    failed_generation_raw,
    failed_publish_raw,
) = sys.argv[1:]

digest_path = Path(digest_raw)
env_path = Path(env_raw)
log_path = Path(log_raw)
overall_rc = int(overall_rc_raw)

def parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]

published = set(parse_csv(published_raw))
failed_generation = set(parse_csv(failed_generation_raw))
failed_publish = set(parse_csv(failed_publish_raw))

spec = importlib.util.spec_from_file_location("hermes_digest_notify", digest_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

if not hasattr(module, "send_telegram") or not callable(module.send_telegram):
    raise SystemExit("digest.py does not expose callable send_telegram(env, text)")

env = {}
for raw in env_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    env[key.strip()] = value

missing = [k for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not env.get(k)]
if missing:
    raise SystemExit("Missing Telegram settings in .env: " + ", ".join(missing))

log_text = ""
if log_path.exists():
    log_text = log_path.read_text(encoding="utf-8", errors="replace")

def generation_error(category: str) -> str:
    marker = f"Sāku kategoriju: {category}"
    start = log_text.rfind(marker)
    if start < 0:
        return "generation failed"
    tail = log_text[start:]
    next_positions = [
        pos for other in ("devops", "ai", "agents")
        if other != category
        for pos in [tail.find(f"Sāku kategoriju: {other}", 1)]
        if pos >= 0
    ]
    if next_positions:
        tail = tail[:min(next_positions)]
    stripped_lines = [line.strip() for line in tail.splitlines()]
    exceptions = [
        line for line in stripped_lines
        if line.startswith(("RuntimeError:", "ValueError:", "KeyError:", "JSONDecodeError:"))
    ]
    if exceptions:
        return exceptions[-1]
    errors = [line for line in stripped_lines if "KĻŪDA:" in line]
    return errors[-1] if errors else "generation failed"

def publish_error(category: str) -> str:
    marker = f"Publicēju: {category}"
    start = log_text.rfind(marker)
    if start < 0:
        return "publication failed"
    tail = log_text[start:]
    next_positions = [
        pos for other in ("devops", "ai", "agents")
        if other != category
        for pos in [tail.find(f"Publicēju: {other}", 1)]
        if pos >= 0
    ]
    if next_positions:
        tail = tail[:min(next_positions)]
    stripped_lines = [line.strip() for line in tail.splitlines()]
    exceptions = [
        line for line in stripped_lines
        if line.startswith(("RuntimeError:", "ValueError:", "KeyError:"))
    ]
    if exceptions:
        return exceptions[-1]
    errors = [line for line in stripped_lines if "KĻŪDA:" in line]
    return errors[-1] if errors else "publication failed"

label = {"devops": "DevOps", "ai": "AI", "agents": "Agents"}
url = {
    "devops": f"https://tech.rozkalns.net/digest/{today}/",
    "ai": f"https://tech.rozkalns.net/ai/{today}/",
    "agents": f"https://tech.rozkalns.net/agents/{today}/",
}

header = "✅ Hermes Tech daily digest" if overall_rc == 0 else "⚠️ Hermes Tech daily digest"
lines = [header, today, "", "Published:"]
errors = []

for category in ("devops", "ai", "agents"):
    if category in published:
        lines.append(f"✅ {label[category]} — {url[category]}")
    elif category in failed_generation:
        lines.append(f"❌ {label[category]} — generation failed")
        errors.append(f"{label[category]}: {generation_error(category)}")
    elif category in failed_publish:
        lines.append(f"❌ {label[category]} — publication failed")
        errors.append(f"{label[category]}: {publish_error(category)}")
    else:
        lines.append(f"❌ {label[category]} — not published")
        errors.append(f"{label[category]}: no final publication status")

if errors:
    lines.extend(["", "Error:", *errors])

lines.extend([
    "",
    "Overall: SUCCESS" if overall_rc == 0 else "Overall: PARTIAL FAILURE",
])

ok = module.send_telegram(env, "\n".join(lines))
raise SystemExit(0 if ok else 1)
PY_TELEGRAM_SUMMARY
telegram_rc=$?
set -e

if (( telegram_rc == 0 )); then
    log "Telegram pipeline kopsavilkums nosūtīts"
else
    log "BRĪDINĀJUMS: Telegram pipeline kopsavilkumu neizdevās nosūtīt (rc=$telegram_rc)"
fi
# END MANAGED: TELEGRAM_PIPELINE_SUMMARY_V2

if (( overall_rc == 0 )); then
    log "Visas ${published_count} kategorijas publicētas veiksmīgi"
else
    log "Pipeline pabeigts daļēji: publicētas ${published_count}"
    log "Ģenerēšanas kļūdas ${#failed_generation[@]}, publicēšanas kļūdas ${publish_failures}"
fi
exit "$overall_rc"
