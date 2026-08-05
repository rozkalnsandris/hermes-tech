#!/usr/bin/env bash
# Hermes Tech — droša digest publicēšana blogā
# Lietošana: publish.sh <devops|ai|agents> [YYYY-MM-DD]
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

# HERMES_PUBLISH_SAFETY_V2
BASE="${HERMES_TECH_ROOT:-$HOME/hermes-tech}"
[[ "$BASE" == /* ]] || {
    echo "KĻŪDA: HERMES_TECH_ROOT jābūt absolūtam ceļam" >&2
    exit 2
}
export HERMES_TECH_ROOT="$BASE"
DB="$BASE/data/hermes.db"
SITE="$BASE/site"
PUBLIC_DIR="$SITE/public"
PYTHON="$BASE/venv/bin/python"
SYNC_HELPER="$BASE/sync_generated_content.sh"
CAT="${1:?Lietošana: publish.sh <devops|ai|agents> [YYYY-MM-DD]}"
DATE="${2:-$(date -u +%Y-%m-%d)}"

[[ "$DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || {
    echo "KĻŪDA: nederīgs datums '$DATE' — vajag YYYY-MM-DD" >&2
    exit 2
}

case "$CAT" in
    devops) SECTION="digest"; TITLE="What mattered in DevOps yesterday — $DATE" ;;
    ai)     SECTION="ai"; TITLE="What mattered in AI yesterday — $DATE" ;;
    agents) SECTION="agents"; TITLE="What mattered in AI agents yesterday — $DATE" ;;
    *) echo "KĻŪDA: nezināma kategorija '$CAT'" >&2; exit 2 ;;
esac

for cmd in flock mktemp hugo git rsync tail sed timeout; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "KĻŪDA: nav atrasta komanda '$cmd'" >&2
        exit 1
    }
done
[[ -x "$PYTHON" ]] || { echo "KĻŪDA: nav izpildāms $PYTHON" >&2; exit 1; }
[[ -f "$DB" ]] || { echo "KĻŪDA: nav datubāzes $DB" >&2; exit 1; }
[[ -f "$SYNC_HELPER" ]] || {
    echo "KĻŪDA: nav GitHub sinhronizācijas helpera $SYNC_HELPER" >&2
    exit 1
}
bash -n "$SYNC_HELPER" || {
    echo "KĻŪDA: GitHub sinhronizācijas helperim ir Bash sintakses kļūda" >&2
    exit 1
}

exec 9>"$BASE/.publish.lock"
flock -w 30 9 || {
    echo "KĻŪDA: 30 sekundēs neizdevās iegūt publicēšanas lock" >&2
    exit 75
}

SRC="$BASE/digests/$DATE-$CAT.md"
# Vecais bez-kategorijas nosaukums ir atļauts tikai DevOps digestam.
if [[ ! -f "$SRC" && "$CAT" == "devops" ]]; then
    SRC="$BASE/digests/$DATE.md"
fi
[[ -f "$SRC" ]] || {
    echo "KĻŪDA: nav digesta faila $BASE/digests/$DATE-$CAT.md" >&2
    exit 1
}

IDS_LINE=$(sed -n '1p' "$SRC")
IDS_RE='^<!--[[:space:]]selected_ids:[[:space:]]([0-9]+(,[0-9]+)*)[[:space:]]-->$'
if [[ "$IDS_LINE" =~ $IDS_RE ]]; then
    SELECTED_IDS="${BASH_REMATCH[1]}"
else
    echo "KĻŪDA: digestā nav derīgas selected_ids metadatu rindas" >&2
    echo "Sagaidīts pirmajā rindā: <!-- selected_ids: 123,456 -->" >&2
    exit 1
fi

DST_DIR="$SITE/content/$SECTION"
DST="$DST_DIR/$DATE.md"
OG="$SITE/static/og/$DATE-$CAT.png"
DIGEST_GIT_PATH="digests/$(basename "$SRC")"
CONTENT_GIT_PATH="site/content/$SECTION/$DATE.md"
OG_GIT_PATH="site/static/og/$DATE-$CAT.png"
GIT_PATHS=("$DIGEST_GIT_PATH" "$CONTENT_GIT_PATH" "$OG_GIT_PATH")

# Pirms jebkuras publicēšanas mutācijas pārbaudām, ka production checkout un
# origin/main ir viens un tas pats commits un darba kokā nav neatļautu failu.
cd "$BASE"
SYNC_BASE_SHA=$(bash "$SYNC_HELPER" preflight "$CAT" "$DATE" "$DIGEST_GIT_PATH")

mkdir -p "$DST_DIR" "$(dirname "$OG")" "$PUBLIC_DIR"

WORK=$(mktemp -d "$BASE/.publish-work.XXXXXXXX")
BUILD_DIR="$WORK/build"
PUBLIC_BACKUP="$WORK/public-backup"
mkdir -p "$BUILD_DIR" "$PUBLIC_BACKUP"

DST_EXISTED=0
OG_EXISTED=0
PUBLIC_BACKED_UP=0
LIVE_CHANGED=0
DB_COMMITTED=0

[[ -e "$DST" ]] && { cp -a "$DST" "$WORK/content.backup"; DST_EXISTED=1; }
[[ -e "$OG" ]] && { cp -a "$OG" "$WORK/og.backup"; OG_EXISTED=1; }
TMP_DST=$(mktemp "$DST_DIR/.${DATE}.md.XXXXXXXX")

restore_files() {
    if (( DST_EXISTED )); then
        cp -a "$WORK/content.backup" "$DST"
    else
        rm -f -- "$DST"
    fi
    if (( OG_EXISTED )); then
        cp -a "$WORK/og.backup" "$OG"
    else
        rm -f -- "$OG"
    fi
    if (( PUBLIC_BACKED_UP )); then
        mkdir -p "$PUBLIC_DIR"
        rsync -a --delete "$PUBLIC_BACKUP/" "$PUBLIC_DIR/"
    fi
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    rm -f -- "${TMP_DST:-}"
    if (( rc != 0 && DB_COMMITTED == 0 )); then
        echo "Publicēšana neizdevās — atjaunoju iepriekšējos vietnes failus." >&2
        restore_files || echo "BRĪDINĀJUMS: automātiska failu atjaunošana neizdevās" >&2
    fi
    rm -rf -- "$WORK"
    exit "$rc"
}
trap cleanup EXIT INT TERM

# HERMES_DESIGN_V4_TOPICS
FORMATTED_BODY="$WORK/formatted-body.md"
tail -n +2 "$SRC" \
    | sed '1{/^#\{1,2\} /d}' \
    | "$PYTHON" "$BASE/format_digest.py" \
    > "$FORMATTED_BODY"

[[ -s "$FORMATTED_BODY" ]] || {
    echo "KĻŪDA: format_digest.py izveidoja tukšu saturu" >&2
    exit 1
}

TOPICS_YAML=$(
    "$PYTHON" - "$FORMATTED_BODY" <<'PYTOPICS'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
topics = []

for line in path.read_text(encoding="utf-8").splitlines():
    match = re.match(r"^\s*#{2,4}\s+(.+?)\s*$", line)
    if not match:
        continue

    topic = match.group(1)
    topic = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", topic)
    topic = re.sub(r"<[^>]+>", "", topic)
    topic = topic.replace("**", "").replace("__", "").replace("`", "")
    topic = re.sub(r"\s+#+\s*$", "", topic)
    topic = re.sub(r"\s+", " ", topic).strip()

    if topic and topic not in topics:
        topics.append(topic)
    if len(topics) == 3:
        break

if len(topics) < 3:
    raise SystemExit(
        f"KĻŪDA: formatētajā digestā atrasti tikai {len(topics)} rakstu virsraksti"
    )

for topic in topics:
    print("  - " + json.dumps(topic, ensure_ascii=False))
PYTOPICS
)

{
    echo "---"
    echo "title: \"$TITLE\""
    echo "date: ${DATE}T07:00:00+02:00"
    echo "images: [\"/og/${DATE}-${CAT}.png\"]"
    echo "topics:"
    printf '%s\n' "$TOPICS_YAML"
    echo "---"
    echo
    cat "$FORMATTED_BODY"
} > "$TMP_DST"
[[ -s "$TMP_DST" ]] || { echo "KĻŪDA: izveidots tukšs Hugo satura fails" >&2; exit 1; }
mv -f -- "$TMP_DST" "$DST"
TMP_DST=""

"$PYTHON" "$BASE/ogcard.py" "$DATE-$CAT" "$TITLE"
[[ -s "$OG" ]] || { echo "KĻŪDA: OG attēls netika izveidots: $OG" >&2; exit 1; }

# Vispirms tikai validējam DB ierakstus, neko nemainot.
# HERMES_PRIMARY_CATEGORY_PUBLISH_V1
# primary_category is the router-owned source of truth for digest category.
"$PYTHON" - "$DB" "$DATE" "$CAT" "$SELECTED_IDS" validate <<'PY'
import sqlite3
import sys
from pathlib import Path

raw_db, digest_date, category, raw_ids, mode = sys.argv[1:]
ids = list(dict.fromkeys(int(value) for value in raw_ids.split(",")))
if not ids or any(value <= 0 for value in ids):
    raise SystemExit("KĻŪDA: nederīgi selected_ids")

placeholders = ",".join("?" for _ in ids)
conn = sqlite3.connect(f"file:{Path(raw_db)}?mode=rw", uri=True, timeout=30)
try:
    conn.execute("PRAGMA busy_timeout = 30000")
    rows = conn.execute(
        f"SELECT id, primary_category, digest_date FROM articles WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row[0]: row for row in rows}
    missing = [value for value in ids if value not in by_id]
    wrong = [value for value in ids if value in by_id and by_id[value][1] != category]
    conflicts = [
        (value, by_id[value][2]) for value in ids
        if value in by_id and by_id[value][2] not in (None, digest_date)
    ]
    if missing:
        raise RuntimeError(f"datubāzē nav article ID: {missing}")
    if wrong:
        raise RuntimeError(f"article ID neatbilst kategorijai {category}: {wrong}")
    if conflicts:
        raise RuntimeError(f"article ID jau piesaistīti citam digestam: {conflicts}")
finally:
    conn.close()
PY

# Hugo būvē pilnu vietnes kopiju atsevišķā direktorijā.
cd "$SITE"
hugo --destination "$BUILD_DIR" --cleanDestinationDir --minify --quiet

# HERMES_PUBLIC_PERMISSIONS_V5
# Hugo būve notiek ar umask 077, tādēļ pirms rsync normalizējam tikai
# publiski pasniedzamās būves tiesības. Avota faili paliek privāti.
find "$BUILD_DIR" -type d -exec chmod 755 {} +
find "$BUILD_DIR" -type f -exec chmod 644 {} +

[[ -s "$BUILD_DIR/index.html" ]] || {
    echo "KĻŪDA: Hugo pagaidu būvē nav index.html" >&2
    exit 1
}

# Saglabājam pašreizējo public direktoriju un tikai tad izvietojam pārbaudīto būvi.
rsync -a "$PUBLIC_DIR/" "$PUBLIC_BACKUP/"
PUBLIC_BACKED_UP=1
rsync -a --delete "$BUILD_DIR/" "$PUBLIC_DIR/"

# Galamērķa sakne var saglabāt veco 0700 režīmu, tādēļ normalizējam arī to.
chmod 755 "$PUBLIC_DIR"
find "$PUBLIC_DIR" -type d -exec chmod 755 {} +
find "$PUBLIC_DIR" -type f -exec chmod 644 {} +

LIVE_CHANGED=1

# DB atjauninājums notiek pēc veiksmīgas būves un dzīvo failu izvietošanas.
# Ja tas neizdodas, EXIT trap atjauno veco public/content/OG stāvokli.
"$PYTHON" - "$DB" "$DATE" "$CAT" "$SELECTED_IDS" update <<'PY'
import sqlite3
import sys
from pathlib import Path

raw_db, digest_date, category, raw_ids, mode = sys.argv[1:]
ids = list(dict.fromkeys(int(value) for value in raw_ids.split(",")))
placeholders = ",".join("?" for _ in ids)
conn = sqlite3.connect(f"file:{Path(raw_db)}?mode=rw", uri=True, timeout=30)
try:
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute(
        f"SELECT id, source, primary_category, digest_date FROM articles "
        f"WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {row[0]: row for row in rows}
    missing = [value for value in ids if value not in by_id]
    wrong = [value for value in ids if value in by_id and by_id[value][2] != category]
    conflicts = [
        (value, by_id[value][3]) for value in ids
        if value in by_id and by_id[value][3] not in (None, digest_date)
    ]
    if missing or wrong or conflicts:
        raise RuntimeError(
            f"DB validācija mainījās; missing={missing}, wrong={wrong}, conflicts={conflicts}"
        )
    pending = [value for value in ids if by_id[value][3] is None]
    if pending:
        pending_q = ",".join("?" for _ in pending)
        conn.execute(
            f"""UPDATE sources SET picked = picked + 1
                WHERE name IN (
                    SELECT DISTINCT source FROM articles
                    WHERE id IN ({pending_q}) AND digest_date IS NULL
                )""",
            pending,
        )
        conn.execute(
            f"UPDATE articles SET digest_date = ? "
            f"WHERE id IN ({pending_q}) AND digest_date IS NULL",
            [digest_date, *pending],
        )
    conn.commit()
    print(f"DB OK: {len(pending)} jauni, {len(ids) - len(pending)} jau atzīmēti")
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()
PY
DB_COMMITTED=1

echo "Publicēts: https://tech.rozkalns.net/$SECTION/$DATE/"

cd "$BASE"
# Stage tikai šīs publikācijas trīs atļautos failus. Neizmantojam -A, lai
# sibling digesti, backup vai citas nejaušas izmaiņas nevar nonākt commitā.
if ! git add -- "${GIT_PATHS[@]}"; then
    echo "KĻŪDA: git add neizdevās; lapa un DB jau ir atjauninātas, GitHub sinhronizācija nav veikta" >&2
    exit 76
fi

if git diff --cached --quiet; then
    if ! bash "$SYNC_HELPER" verify-noop "$CAT" "$DATE" "$DIGEST_GIT_PATH" "$SYNC_BASE_SHA"; then
        echo "KĻŪDA: GitHub no-op verifikācija neizdevās; publicētā lapa un DB netiek atgrieztas atpakaļ" >&2
        exit 76
    fi
    echo "GitHub sinhronizācija: nav jauna content commita"
    exit 0
fi

if ! bash "$SYNC_HELPER" verify-index "$CAT" "$DATE" "$DIGEST_GIT_PATH" "$SYNC_BASE_SHA"; then
    echo "KĻŪDA: staged publikācijas ceļu pārbaude neizdevās; lapa un DB jau ir atjauninātas" >&2
    exit 76
fi

if ! git commit -q -m "Publish $CAT digest $DATE"; then
    echo "KĻŪDA: git commit neizdevās; lapa un DB jau ir atjauninātas, staged faili saglabāti" >&2
    exit 76
fi

PUBLISH_COMMIT_SHA=$(git rev-parse HEAD)
if ! bash "$SYNC_HELPER" sync "$CAT" "$DATE" "$DIGEST_GIT_PATH" "$SYNC_BASE_SHA" "$PUBLISH_COMMIT_SHA"; then
    echo "KĻŪDA: GitHub sinhronizācija neizdevās; publicētā lapa un DB saglabātas, lokālais commits $PUBLISH_COMMIT_SHA nav dzēsts" >&2
    exit 76
fi

echo "GitHub sinhronizēts: $PUBLISH_COMMIT_SHA"
