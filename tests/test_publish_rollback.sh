#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PUBLISH_SCRIPT=${1:-"$REPO_ROOT/publish.sh"}
SYNC_SCRIPT=${2:-"$REPO_ROOT/sync_generated_content.sh"}
GITIGNORE_FILE=${3:-"$REPO_ROOT/.gitignore"}

for required in "$PUBLISH_SCRIPT" "$SYNC_SCRIPT" "$GITIGNORE_FILE"; do
    [[ -f "$required" ]] || { echo "missing test input: $required" >&2; exit 2; }
done

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

export GIT_AUTHOR_NAME='Hermes Test'
export GIT_AUTHOR_EMAIL='hermes-test@example.invalid'
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
export HERMES_GIT_SYNC_TIMEOUT_SECONDS=5

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

remote="$TMP/rollback.git"
seed="$TMP/seed"
prod="$TMP/prod"
fakebin="$TMP/fakebin"
real_python=$(command -v python3)

git init --bare --quiet "$remote"
git init --quiet --initial-branch=main "$seed"
git -C "$seed" config user.name "$GIT_AUTHOR_NAME"
git -C "$seed" config user.email "$GIT_AUTHOR_EMAIL"
printf 'base\n' > "$seed/base.txt"
git -C "$seed" add -- base.txt
git -C "$seed" commit --quiet -m 'Initial base'
git -C "$seed" remote add origin "$remote"
git -C "$seed" push --quiet -u origin main
git --git-dir="$remote" symbolic-ref HEAD refs/heads/main

git clone --quiet "$remote" "$prod"
git -C "$prod" config user.name "$GIT_AUTHOR_NAME"
git -C "$prod" config user.email "$GIT_AUTHOR_EMAIL"
mkdir -p "$fakebin" "$prod/venv/bin" "$prod/data" "$prod/logs" \
    "$prod/digests" "$prod/site/content/digest" "$prod/site/static/og" \
    "$prod/site/public"
cp "$PUBLISH_SCRIPT" "$prod/publish.sh"
cp "$SYNC_SCRIPT" "$prod/sync_generated_content.sh"
cp "$GITIGNORE_FILE" "$prod/.gitignore"

cat > "$prod/format_digest.py" <<'PY_FORMAT'
import sys
sys.stdout.write(sys.stdin.read())
PY_FORMAT

cat > "$prod/ogcard.py" <<'PY_OG'
import os
from pathlib import Path
import sys

root = Path(os.environ["HERMES_TECH_ROOT"])
path = root / "site" / "static" / "og" / f"{sys.argv[1]}.png"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(b"new-og")
PY_OG

cat > "$prod/venv/bin/python" <<PY_WRAPPER
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "\${1:-}" == "-" && "\${*: -1}" == "update" ]]; then
    cat >/dev/null
    echo 'synthetic DB update failure after live deployment' >&2
    exit 42
fi
exec "$real_python" "\$@"
PY_WRAPPER
chmod 700 "$prod/venv/bin/python"

cat > "$fakebin/hugo" <<'SH_HUGO'
#!/usr/bin/env bash
set -Eeuo pipefail
destination=""
while (( $# > 0 )); do
    case "$1" in
        --destination)
            destination=$2
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done
[[ -n "$destination" ]]
mkdir -p "$destination/assets"
printf '<html>new-public</html>\n' > "$destination/index.html"
printf 'new-asset\n' > "$destination/assets/new.txt"
SH_HUGO
chmod 700 "$fakebin/hugo"

printf 'old-content\n' > "$prod/site/content/digest/2026-08-05.md"
printf 'old-og\n' > "$prod/site/static/og/2026-08-05-devops.png"
printf '<html>old-public</html>\n' > "$prod/site/public/index.html"
printf 'keep-me\n' > "$prod/site/public/old-only.txt"

cat > "$prod/digests/2026-08-05-devops.md" <<'MD_DIGEST'
<!-- selected_ids: 1,2,3 -->
# What mattered in DevOps yesterday — 2026-08-05

## First topic

First body.

## Second topic

Second body.

## Third topic

Third body.
MD_DIGEST

"$real_python" - "$prod/data/hermes.db" <<'PY_DB_CREATE'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
conn.executescript(
    """
    CREATE TABLE articles (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        primary_category TEXT,
        digest_date TEXT
    );
    CREATE TABLE sources (
        name TEXT PRIMARY KEY,
        picked INTEGER NOT NULL DEFAULT 0
    );
    INSERT INTO sources(name, picked) VALUES ('test-source', 0);
    INSERT INTO articles(id, source, primary_category, digest_date) VALUES
        (1, 'test-source', 'devops', NULL),
        (2, 'test-source', 'devops', NULL),
        (3, 'test-source', 'devops', NULL);
    """
)
conn.commit()
conn.close()
PY_DB_CREATE

git -C "$prod" add -- \
    .gitignore publish.sh sync_generated_content.sh format_digest.py ogcard.py \
    site/content/digest/2026-08-05.md \
    site/static/og/2026-08-05-devops.png
git -C "$prod" commit --quiet -m 'Add rollback publication fixture'
git -C "$prod" push --quiet origin main
baseline=$(git -C "$prod" rev-parse HEAD)

set +e
HERMES_TECH_ROOT="$prod" PATH="$fakebin:$PATH" \
    bash "$prod/publish.sh" devops 2026-08-05 \
    >"$TMP/publish.out" 2>"$TMP/publish.err"
rc=$?
set -e

(( rc != 0 )) || fail 'publish unexpectedly succeeded despite synthetic DB failure'
grep -q 'synthetic DB update failure after live deployment' "$TMP/publish.err" || \
    fail 'synthetic DB failure was not reached'
grep -q 'atjaunoju iepriekšējos vietnes failus' "$TMP/publish.err" || \
    fail 'rollback path did not report file restoration'

[[ "$(cat "$prod/site/content/digest/2026-08-05.md")" == 'old-content' ]] || \
    fail 'content source was not restored'
[[ "$(cat "$prod/site/static/og/2026-08-05-devops.png")" == 'old-og' ]] || \
    fail 'OG image was not restored'
[[ "$(cat "$prod/site/public/index.html")" == '<html>old-public</html>' ]] || \
    fail 'public index was not restored'
[[ "$(cat "$prod/site/public/old-only.txt")" == 'keep-me' ]] || \
    fail 'public-only previous file was not restored'
[[ ! -e "$prod/site/public/assets/new.txt" ]] || \
    fail 'new public asset survived rollback'

"$real_python" - "$prod/data/hermes.db" <<'PY_DB_VERIFY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
rows = conn.execute(
    "SELECT id, digest_date FROM articles ORDER BY id"
).fetchall()
picked = conn.execute(
    "SELECT picked FROM sources WHERE name='test-source'"
).fetchone()[0]
conn.close()
if rows != [(1, None), (2, None), (3, None)]:
    raise SystemExit(f"DB changed despite rollback: {rows}")
if picked != 0:
    raise SystemExit(f"source picked changed despite rollback: {picked}")
PY_DB_VERIFY

[[ "$(git -C "$prod" rev-parse HEAD)" == "$baseline" ]] || \
    fail 'rollback failure created a local commit'
[[ "$(git --git-dir="$remote" rev-parse refs/heads/main)" == "$baseline" ]] || \
    fail 'rollback failure changed remote main'
git -C "$prod" diff --quiet -- \
    site/content/digest/2026-08-05.md \
    site/static/og/2026-08-05-devops.png || \
    fail 'tracked publication files remain modified after rollback'
if find "$prod" -maxdepth 1 -type d -name '.publish-work.*' | grep -q .; then
    fail 'temporary publication work directory was not removed'
fi

printf 'PASS: failed DB update restored content, OG, public tree, DB, and Git state\n'
