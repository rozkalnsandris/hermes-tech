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

remote="$TMP/publish.git"
seed="$TMP/seed"
publish_home="$TMP/home"
prod="$publish_home/hermes-tech"
fakebin="$TMP/fakebin"

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
from pathlib import Path
import sys

path = (
    Path.home()
    / "hermes-tech"
    / "site"
    / "static"
    / "og"
    / f"{sys.argv[1]}.png"
)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(b"test-og")
PY_OG

ln -s "$(command -v python3)" "$prod/venv/bin/python"

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
mkdir -p "$destination"
printf '<html>test</html>\n' > "$destination/index.html"
SH_HUGO
chmod 700 "$fakebin/hugo"

git -C "$prod" add -- \
    .gitignore publish.sh sync_generated_content.sh format_digest.py ogcard.py
git -C "$prod" commit --quiet -m 'Add publication runtime'
git -C "$prod" push --quiet origin main

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
printf 'pending ai\n' > "$prod/digests/2026-08-05-ai.md"
printf 'pending agents\n' > "$prod/digests/2026-08-05-agents.md"

python3 - "$prod/data/hermes.db" <<'PY_DB_CREATE'
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

set +e
HOME="$publish_home" PATH="$fakebin:$PATH" \
    bash "$prod/publish.sh" devops 2026-08-05 \
    >"$TMP/publish.out" 2>"$TMP/publish.err"
publish_rc=$?
set -e
if (( publish_rc != 0 )); then
    cat "$TMP/publish.out" >&2 || true
    cat "$TMP/publish.err" >&2 || true
    fail "publish.sh integration failed with rc=$publish_rc"
fi

publish_head=$(git -C "$prod" rev-parse HEAD)
publish_remote_head=$(git --git-dir="$remote" rev-parse refs/heads/main)
[[ "$publish_head" == "$publish_remote_head" ]] || \
    fail 'publish.sh did not leave local and remote at the same SHA'
[[ "$(git -C "$prod" show -s --format=%s HEAD)" == \
   'Publish devops digest 2026-08-05' ]] || \
    fail 'publish.sh created an unexpected commit subject'

git --git-dir="$remote" show \
    "$publish_remote_head:site/content/digest/2026-08-05.md" >/dev/null || \
    fail 'publish.sh content file was not synchronized'
git --git-dir="$remote" show \
    "$publish_remote_head:site/static/og/2026-08-05-devops.png" >/dev/null || \
    fail 'publish.sh OG file was not synchronized'
if git --git-dir="$remote" cat-file -e \
    "$publish_remote_head:digests/2026-08-05-ai.md" 2>/dev/null; then
    fail 'publish.sh leaked a sibling digest into the publication commit'
fi

python3 - "$prod/data/hermes.db" <<'PY_DB_VERIFY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
rows = conn.execute(
    "SELECT id, digest_date FROM articles ORDER BY id"
).fetchall()
picked = conn.execute(
    "SELECT picked FROM sources WHERE name = 'test-source'"
).fetchone()[0]
conn.close()

expected = [(1, '2026-08-05'), (2, '2026-08-05'), (3, '2026-08-05')]
if rows != expected:
    raise SystemExit(f"unexpected article state: {rows}")
if picked != 1:
    raise SystemExit(f"unexpected source picked count: {picked}")
PY_DB_VERIFY

grep -q 'HERMES_GIT_SYNC_OK' "$TMP/publish.out" || \
    fail 'publish.sh did not report verified Git synchronization'

printf 'PASS: real publish.sh completed publication, DB update, commit, push, and SHA verification\n'
