#!/usr/bin/env python3
"""Hermes Tech — RSS collector v3.
Jaunumi: kategorijas (devops/ai/agents) + pilnā satura glabāšana.
Migrācija: automātiski pievieno category/content kolonnas vecai DB.
"""
import re
import socket
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser

BASE = Path.home() / "hermes-tech"
DB = BASE / "data" / "hermes.db"
FEEDS = BASE / "feeds.txt"
LOG = BASE / "logs" / "collector.log"

MAX_CONTENT = 40000  # rakstzīmes pilnajam tekstam

feedparser.USER_AGENT = "HermesTech/1.0 (+https://tech.rozkalns.net)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL UNIQUE,
    published TEXT,
    summary TEXT,
    fetched_at TEXT NOT NULL,
    digest_date TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    fetch_ok INTEGER DEFAULT 0,
    fetch_fail INTEGER DEFAULT 0,
    collected INTEGER DEFAULT 0,
    picked INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at);
"""

MIGRATIONS = [
    "ALTER TABLE articles ADD COLUMN category TEXT DEFAULT 'devops'",
    "ALTER TABLE articles ADD COLUMN content TEXT",
    "CREATE INDEX IF NOT EXISTS idx_articles_cat ON articles(category)",
]


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def migrate(conn) -> None:
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # kolonna/indekss jau eksistē
    conn.commit()


# V4: routing columns — idempotent (pārbauda vai kolonna jau eksistē)
ROUTING_COLUMNS = {
    "primary_category": "TEXT",
    "topic_key": "TEXT",
    "routed_at": "TEXT",
}


def migrate_routing(conn) -> None:
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(articles)").fetchall()
    }
    for col, col_type in ROUTING_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {col_type}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_primary_cat "
        "ON articles(primary_category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_topic_key "
        "ON articles(topic_key)"
    )
    conn.commit()


def load_feeds() -> list[tuple[str, str, str]]:
    feeds = []
    for raw in FEEDS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        name, url = parts[0], parts[1]
        cat = parts[2] if len(parts) > 2 and parts[2] else "devops"
        feeds.append((name, url, cat))
    return feeds


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def entry_texts(entry) -> tuple[str, str]:
    """Atgriež (summary_500, full_content). Pilnais: content > summary."""
    full = ""
    if getattr(entry, "content", None):
        try:
            full = entry.content[0].value or ""
        except (IndexError, AttributeError):
            full = ""
    if not full:
        full = getattr(entry, "summary", "") or ""
    full = strip_html(full)[:MAX_CONTENT]
    return full[:500], full


def entry_published(entry) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            return datetime.fromtimestamp(time.mktime(tp), tz=timezone.utc).isoformat()
    return ""


# HERMES_CRON_SAFETY_V1
# feedparser HTTP savienojumiem nepieļaujam bezgalīgu socket gaidīšanu.
socket.setdefaulttimeout(30)


def main() -> int:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(SCHEMA)
    migrate(conn)
    migrate_routing(conn)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_new = 0
    feeds_ok = 0
    feeds_failed = 0

    for name, url, cat in load_feeds():
        conn.execute("INSERT OR IGNORE INTO sources(name) VALUES (?)", (name,))
        try:
            parsed = feedparser.parse(url)
            if parsed.bozo and not parsed.entries:
                raise RuntimeError(f"bozo: {parsed.bozo_exception}")
            new = 0
            for e in parsed.entries[:30]:
                link = getattr(e, "link", "") or ""
                title = (getattr(e, "title", "") or "").strip()
                if not link or not title:
                    continue
                summary, content = entry_texts(e)
                cur = conn.execute(
                    "INSERT OR IGNORE INTO articles"
                    "(source, title, link, published, summary, fetched_at,"
                    " category, content)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (name, title, link, entry_published(e), summary, now,
                     cat, content),
                )
                new += cur.rowcount
            conn.execute(
                "UPDATE sources SET fetch_ok = fetch_ok + 1,"
                " collected = collected + ? WHERE name = ?",
                (new, name),
            )
            total_new += new
            feeds_ok += 1
            log(f"OK   [{cat}] {name}: +{new} jauni ({len(parsed.entries)} feedā)")
        except Exception as exc:  # noqa: BLE001
            feeds_failed += 1
            conn.execute(
                "UPDATE sources SET fetch_fail = fetch_fail + 1 WHERE name = ?",
                (name,),
            )
            log(f"FAIL [{cat}] {name}: {exc}")
        conn.commit()

    conn.close()
    log(
        f"Kopā jauni raksti: {total_new}; "
        f"avoti OK: {feeds_ok}; avoti FAIL: {feeds_failed}"
    )
    if feeds_ok == 0:
        log("KĻŪDA: neviens RSS avots netika veiksmīgi apstrādāts")
        return 1
    if feeds_failed > feeds_ok:
        log(
            "KĻŪDA: neizdevās apstrādāt vairāk nekā pusi RSS avotu "
            f"({feeds_failed} FAIL pret {feeds_ok} OK)"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
