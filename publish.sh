#!/usr/bin/env bash
# Hermes Tech — digest publicēšana blogā (approval solis)
# Lietošana: publish.sh <devops|ai|agents> [YYYY-MM-DD]
set -euo pipefail

BASE="$HOME/hermes-tech"
CAT="${1:?Lietošana: publish.sh <devops|ai|agents> [YYYY-MM-DD]}"
DATE="${2:-$(date -u +%Y-%m-%d)}"

case "$CAT" in
    devops) SECTION="digest"; TITLE="What mattered in DevOps yesterday — $DATE" ;;
    ai)     SECTION="ai";     TITLE="What mattered in AI yesterday — $DATE" ;;
    agents) SECTION="agents"; TITLE="What mattered in AI agents yesterday — $DATE" ;;
    *) echo "KĻŪDA: nezināma kategorija '$CAT'"; exit 1 ;;
esac

SRC="$BASE/digests/$DATE-$CAT.md"
# Atpakaļsaderība ar veco devops nosaukumu bez kategorijas
[ -f "$SRC" ] || SRC="$BASE/digests/$DATE.md"
[ -f "$SRC" ] || { echo "KĻŪDA: nav $BASE/digests/$DATE-$CAT.md"; exit 1; }

DST_DIR="$BASE/site/content/$SECTION"
DST="$DST_DIR/$DATE.md"
mkdir -p "$DST_DIR"

{
  echo "---"
  echo "title: \"$TITLE\""
  echo "date: ${DATE}T07:00:00+02:00"
  echo "images: [\"/og/${DATE}-${CAT}.png\"]"
  echo "---"
  echo
  # Izmetam pirmo H1/H2 (dublē virsrakstu), tad pārstrukturējam vienumus
  # atsevišķos blokos (h3/body/link/citāts), jo modeļa vienkāršie \n
  # Markdown/Goldmark renderī saplūst vienā rindkopā.
  sed '1{/^#\{1,2\} /d}' "$SRC" | "$BASE/venv/bin/python" "$BASE/format_digest.py"
} > "$DST"

"$BASE/venv/bin/python" "$BASE/ogcard.py" "$DATE-$CAT" "$TITLE"

cd "$BASE/site"
hugo --minify --quiet
echo "Publicēts: https://tech.rozkalns.net/$SECTION/$DATE/"

cd "$BASE"
git add -A && git commit -q -m "Publish $CAT digest $DATE" || true
