#!/usr/bin/env python3
"""Hermes Tech RSS collector with explicit SQLite schema versioning."""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import feedparser

from hermes_db import SchemaError, ensure_current_schema
from rss_transport import fetch_feed

BASE = Path.home() / "hermes-tech"
DB = BASE / "data" / "hermes.db"
FEEDS = BASE / "feeds.txt"
LOG = BASE / "logs" / "collector.log"

MAX_CONTENT = 40000


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def open_database() -> sqlite3.Connection:
    """Open a current DB; initialize only when the file is logically empty."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        ensure_current_schema(conn, allow_initialize=True)
        return conn
    except Exception:
        conn.close()
        raise


def load_feeds() -> list[tuple[str, str, str]]:
    feeds = []
    for raw in FEEDS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        name, url = parts[0], parts[1]
        cat = parts[2] if len(parts) > 2 and parts[2] else "devops"
        feeds.append((name, url, cat))
    return feeds


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def entry_texts(entry) -> tuple[str, str]:
    """Return ``(summary_500, full_content)``; content wins over summary."""
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
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime.fromtimestamp(
                time.mktime(parsed),
                tz=timezone.utc,
            ).isoformat()
    return ""


def main() -> int:
    try:
        conn = open_database()
    except (OSError, sqlite3.Error, SchemaError) as exc:
        log(
            "KĻŪDA: SQLite shēma nav gatava collector darbam: "
            f"{exc}. Palaid tools/sqlite_schema.py preflight un atsevišķi "
            "apstiprinātu apply soli."
        )
        return 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_new = 0
    feeds_ok = 0
    feeds_failed = 0

    for name, url, cat in load_feeds():
        conn.execute("INSERT OR IGNORE INTO sources(name) VALUES (?)", (name,))
        try:
            fetched = fetch_feed(url)
            parsed = feedparser.parse(fetched.body)
            if parsed.bozo and not parsed.entries:
                raise RuntimeError(f"bozo: {parsed.bozo_exception}")
            new = 0
            for entry in parsed.entries[:30]:
                link = getattr(entry, "link", "") or ""
                title = (getattr(entry, "title", "") or "").strip()
                if not link or not title:
                    continue
                summary, content = entry_texts(entry)
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO articles"
                    "(source, title, link, published, summary, fetched_at,"
                    " category, content) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        name,
                        title,
                        link,
                        entry_published(entry),
                        summary,
                        now,
                        cat,
                        content,
                    ),
                )
                new += cursor.rowcount
            conn.execute(
                "UPDATE sources SET fetch_ok = fetch_ok + 1,"
                " collected = collected + ? WHERE name = ?",
                (new, name),
            )
            total_new += new
            feeds_ok += 1
            final_host = urlsplit(fetched.final_url).hostname or "unknown"
            media_type = fetched.content_type or "unspecified"
            log(
                f"OK   [{cat}] {name}: +{new} jauni "
                f"({len(parsed.entries)} feedā); http={fetched.status_code} "
                f"bytes={len(fetched.body)} redirects={fetched.redirects} "
                f"type={media_type} host={final_host}"
            )
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
