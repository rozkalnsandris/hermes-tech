#!/usr/bin/env python3
"""Hermes Tech — RSS collector.
Savāc rakstus no feeds.txt, deduplicē pēc linka, glabā SQLite.
Palaiž cron vairākas reizes dienā. Kļūdas tiek logotas, nevis apturētas.
"""
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

feedparser.USER_AGENT = "HermesTech/1.0 (+https://rozkalns.net)"

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


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_feeds() -> list[tuple[str, str]]:
    feeds = []
    for raw in FEEDS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        name, url = line.split("|", 1)
        feeds.append((name.strip(), url.strip()))
    return feeds


def entry_summary(entry) -> str:
    text = getattr(entry, "summary", "") or ""
    # Noņemam HTML tagus rupji un apgriežam
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def entry_published(entry) -> str:
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            return datetime.fromtimestamp(time.mktime(tp), tz=timezone.utc).isoformat()
    return ""


def main() -> int:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_new = 0

    for name, url in load_feeds():
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
                cur = conn.execute(
                    "INSERT OR IGNORE INTO articles"
                    "(source, title, link, published, summary, fetched_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (name, title, link, entry_published(e), entry_summary(e), now),
                )
                new += cur.rowcount
            conn.execute(
                "UPDATE sources SET fetch_ok = fetch_ok + 1,"
                " collected = collected + ? WHERE name = ?",
                (new, name),
            )
            total_new += new
            log(f"OK   {name}: +{new} jauni ({len(parsed.entries)} feedā)")
        except Exception as exc:  # noqa: BLE001 — vienam feedam krītot, pārējie turpina
            conn.execute(
                "UPDATE sources SET fetch_fail = fetch_fail + 1 WHERE name = ?",
                (name,),
            )
            log(f"FAIL {name}: {exc}")
        conn.commit()

    conn.close()
    log(f"Kopā jauni raksti: {total_new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
