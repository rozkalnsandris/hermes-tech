#!/usr/bin/env bash
# Hermes Tech — digest publicēšana blogā (approval solis)
# Lietošana: publish.sh [YYYY-MM-DD]   (bez argumenta = šodiena UTC)
set -euo pipefail

BASE="$HOME/hermes-tech"
DATE="${1:-$(date -u +%Y-%m-%d)}"
SRC="$BASE/digests/$DATE.md"
DST_DIR="$BASE/site/content/digest"
DST="$DST_DIR/$DATE.md"

[ -f "$SRC" ] || { echo "KĻŪDA: nav $SRC"; exit 1; }
mkdir -p "$DST_DIR"

TITLE="What mattered in DevOps yesterday — $DATE"

{
  echo "---"
  echo "title: \"$TITLE\""
  echo "date: ${DATE}T07:00:00+02:00"
  echo "images: [\"/og/${DATE}.png\"]"
  echo "---"
  echo
  # Izmetam pirmo H1 rindu, ja tā dublē virsrakstu
  sed '1{/^# /d}' "$SRC"
} > "$DST"

"$BASE/venv/bin/python" "$BASE/ogcard.py" "$DATE" "$TITLE"

cd "$BASE/site"
hugo --minify --quiet
echo "Publicēts: https://tech.rozkalns.net/digest/$DATE/"

cd "$BASE"
git add -A && git commit -q -m "Publish digest $DATE" || true
