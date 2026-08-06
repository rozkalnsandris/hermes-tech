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

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run installer through sudo'
IFS= read -r RUNNER_TOKEN || fail 'runner registration token was not provided on stdin'
[[ -n "$RUNNER_TOKEN" && "$RUNNER_TOKEN" != *[[:space:]]* ]] || fail 'runner registration token format is invalid'

RUNNER_USER='github-tech-runner'
RUNNER_HOME='/home/github-tech-runner'
RUNNER_DIR="$RUNNER_HOME/actions-runner"
SOURCE_RUNNER='/home/github-release-runner/actions-runner'
REPOSITORY_URL='https://github.com/rozkalnsandris/hermes-tech'
RUNNER_NAME='rpi5-hermes-tech-release'
RUNNER_LABEL='hermes-tech-release'
SERVICE='actions.runner.rozkalnsandris-hermes-tech.rpi5-hermes-tech-release.service'

for command_name in chown cp find grep id install mktemp python3 rm runuser systemctl tar tr useradd; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done

if id "$RUNNER_USER" >/dev/null 2>&1 && [[ -f "$RUNNER_DIR/.runner" ]]; then
    systemctl is-active --quiet "$SERVICE" || fail 'existing Hermes Tech runner service is not active'
    if id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -Fxq docker; then
        fail 'Hermes Tech runner must not belong to docker group'
    fi
    printf 'TECH_RUNNER_INSTALL_RESULT=ALREADY_ACTIVE\n'
    printf 'RUNNER_SERVICE=%s\n' "$SERVICE"
    printf 'RUNNER_HAS_DOCKER_GROUP=false\n'
    exit 0
fi

[[ -d "$SOURCE_RUNNER" && -x "$SOURCE_RUNNER/config.sh" ]] \
    || fail 'existing Hermes Deals runner distribution is unavailable'
[[ -x "$SOURCE_RUNNER/bin/Runner.Listener" ]] \
    || fail 'existing runner listener is unavailable'
RUNNER_VERSION=$("$SOURCE_RUNNER/bin/Runner.Listener" --version)
python3 - "$RUNNER_VERSION" <<'PY'
import sys

parts = tuple(int(value) for value in sys.argv[1].split("."))
if parts < (2, 327, 1):
    raise SystemExit("existing runner is too old; need at least 2.327.1")
PY

if ! id "$RUNNER_USER" >/dev/null 2>&1; then
    useradd \
        --create-home \
        --home-dir "$RUNNER_HOME" \
        --shell /usr/sbin/nologin \
        "$RUNNER_USER"
fi

install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_DIR"
TMP_COPY=$(mktemp -d /tmp/hermes-tech-runner-copy.XXXXXXXX)
cleanup() {
    rm -rf -- "$TMP_COPY"
}
trap cleanup EXIT

tar \
    --exclude='./.credentials' \
    --exclude='./.credentials_rsaparams' \
    --exclude='./.runner' \
    --exclude='./.service' \
    --exclude='./_diag' \
    --exclude='./_work' \
    -C "$SOURCE_RUNNER" -cf - . \
    | tar -C "$TMP_COPY" -xf -

find "$TMP_COPY" -mindepth 1 -maxdepth 1 -exec cp -a -- {} "$RUNNER_DIR/" \;
chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"

runuser -u "$RUNNER_USER" -- env \
    HOME="$RUNNER_HOME" \
    PATH='/usr/local/bin:/usr/bin:/bin' \
    "$RUNNER_DIR/config.sh" \
    --unattended \
    --replace \
    --url "$REPOSITORY_URL" \
    --token "$RUNNER_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$RUNNER_LABEL" \
    --work _work

(
    cd "$RUNNER_DIR"
    ./svc.sh install "$RUNNER_USER"
    ./svc.sh start
)

systemctl is-active --quiet "$SERVICE" || fail 'Hermes Tech runner service did not start'
if id -nG "$RUNNER_USER" | tr ' ' '\n' | grep -Fxq docker; then
    fail 'Hermes Tech runner must not belong to docker group'
fi

printf 'TECH_RUNNER_INSTALL_RESULT=PASS\n'
printf 'RUNNER_VERSION=%s\n' "$RUNNER_VERSION"
printf 'RUNNER_SERVICE=%s\n' "$SERVICE"
printf 'RUNNER_HAS_DOCKER_GROUP=false\n'
