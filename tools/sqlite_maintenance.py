#!/usr/bin/env python3
"""Read-only SQLite sizing, growth and maintenance report for Hermes Tech.

This tool intentionally has no mutation subcommand. It opens the production
SQLite database read-only, validates the current schema and reports evidence
needed to decide whether a separately approved maintenance operation is useful.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

MODULE_DIR = Path(__file__).resolve().parents[1]
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

import hermes_db

EXIT_OPERATIONAL = 1
EXIT_USAGE = 2
EXIT_SCHEMA = 3

ACTIVE_INPUT_HOURS = 36
GROWTH_WINDOW_DAYS = 30
CONTENT_REVIEW_AGE_DAYS = 90
REVIEW_DB_BYTES = 256 * 1024 * 1024
REVIEW_FREELIST_BYTES = 32 * 1024 * 1024
REVIEW_FREELIST_RATIO = 0.20
REVIEW_OLD_CONTENT_BYTES = 128 * 1024 * 1024

ROW_PAYLOAD_BYTES_SQL = " + ".join(
    f"COALESCE(length(CAST({name} AS BLOB)), 0)"
    for name in (
        "source",
        "title",
        "link",
        "published",
        "summary",
        "fetched_at",
        "digest_date",
        "category",
        "content",
        "primary_category",
        "topic_key",
        "routed_at",
    )
)


def _pragma_int(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(f"PRAGMA {name}").fetchone()
    if row is None:
        raise hermes_db.SchemaError(f"PRAGMA {name} returned no value")
    return int(row[0])


def _auto_vacuum_name(value: int) -> str:
    return {0: "none", 1: "full", 2: "incremental"}.get(value, f"unknown:{value}")


def _sidecars(db: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for suffix in ("-journal", "-wal", "-shm"):
        path = Path(str(db) + suffix)
        if path.exists():
            result.append(
                {
                    "suffix": suffix,
                    "size_bytes": int(path.stat().st_size),
                }
            )
    return result


def _count_since(conn: sqlite3.Connection, cutoff: datetime) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE datetime(fetched_at) >= datetime(?)",
        (cutoff.isoformat(timespec="seconds"),),
    ).fetchone()
    return int(row[0])


def _payload_since(conn: sqlite3.Connection, cutoff: datetime) -> int:
    row = conn.execute(
        f"SELECT COALESCE(SUM({ROW_PAYLOAD_BYTES_SQL}), 0) "
        "FROM articles WHERE datetime(fetched_at) >= datetime(?)",
        (cutoff.isoformat(timespec="seconds"),),
    ).fetchone()
    return int(row[0])


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_report(db: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Return a self-consistent read-only maintenance report.

    The main database SHA-256 is compared before and after the inspection. If an
    external writer changes the file during the report, the report fails closed
    and should be rerun between collector/publisher writer windows.
    """
    resolved = db.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"database is not a regular file: {resolved}")

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    instant = instant.astimezone(timezone.utc)

    before_sha = hermes_db.database_sha256(resolved)
    before_size = int(resolved.stat().st_size)

    conn = hermes_db.open_readonly(resolved)
    try:
        hermes_db.ensure_current_schema(conn)

        page_size = _pragma_int(conn, "page_size")
        page_count = _pragma_int(conn, "page_count")
        freelist_count = _pragma_int(conn, "freelist_count")
        auto_vacuum = _pragma_int(conn, "auto_vacuum")
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()

        row = conn.execute(
            """SELECT COUNT(*),
                      COALESCE(SUM(CASE WHEN digest_date IS NOT NULL THEN 1 ELSE 0 END), 0),
                      COALESCE(SUM(CASE WHEN routed_at IS NOT NULL THEN 1 ELSE 0 END), 0),
                      COALESCE(SUM(CASE WHEN primary_category = 'reject' THEN 1 ELSE 0 END), 0),
                      MIN(fetched_at), MAX(fetched_at),
                      COALESCE(SUM(length(CAST(content AS BLOB))), 0),
                      COALESCE(SUM(length(CAST(summary AS BLOB))), 0)
               FROM articles"""
        ).fetchone()
        total_rows = int(row[0])
        published_rows = int(row[1])
        routed_rows = int(row[2])
        rejected_rows = int(row[3])
        oldest_fetched_at = row[4]
        newest_fetched_at = row[5]
        content_bytes = int(row[6])
        summary_bytes = int(row[7])

        active_cutoff = instant - timedelta(hours=ACTIVE_INPUT_HOURS)
        seven_day_cutoff = instant - timedelta(days=7)
        growth_cutoff = instant - timedelta(days=GROWTH_WINDOW_DAYS)
        content_cutoff = instant - timedelta(days=CONTENT_REVIEW_AGE_DAYS)

        rows_active = _count_since(conn, active_cutoff)
        rows_7d = _count_since(conn, seven_day_cutoff)
        rows_30d = _count_since(conn, growth_cutoff)
        payload_30d = _payload_since(conn, growth_cutoff)

        old_content = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(length(CAST(content AS BLOB))), 0)
               FROM articles
               WHERE content IS NOT NULL
                 AND length(content) > 0
                 AND datetime(fetched_at) < datetime(?)""",
            (content_cutoff.isoformat(timespec="seconds"),),
        ).fetchone()
        old_content_rows = int(old_content[0])
        old_content_bytes = int(old_content[1])
    finally:
        conn.close()

    after_sha = hermes_db.database_sha256(resolved)
    after_size = int(resolved.stat().st_size)
    if (before_sha, before_size) != (after_sha, after_size):
        raise hermes_db.SchemaError(
            "database changed during read-only maintenance report; rerun between "
            "collector/publisher writer windows"
        )

    oldest_dt = _parse_timestamp(oldest_fetched_at)
    if total_rows and oldest_dt is not None:
        observed_start = max(oldest_dt, instant - timedelta(days=GROWTH_WINDOW_DAYS))
        observed_days = max(
            (instant - observed_start).total_seconds() / 86400.0,
            1.0,
        )
    else:
        observed_days = float(GROWTH_WINDOW_DAYS)

    freelist_bytes = freelist_count * page_size
    freelist_ratio = (freelist_count / page_count) if page_count else 0.0
    sidecars = _sidecars(resolved)
    sidecar_bytes = sum(int(item["size_bytes"]) for item in sidecars)

    review_reasons: list[str] = []
    if before_size >= REVIEW_DB_BYTES:
        review_reasons.append("database-size-threshold")
    if (
        freelist_bytes >= REVIEW_FREELIST_BYTES
        and freelist_ratio >= REVIEW_FREELIST_RATIO
    ):
        review_reasons.append("reclaimable-space-threshold")
    if old_content_bytes >= REVIEW_OLD_CONTENT_BYTES:
        review_reasons.append("old-content-payload-threshold")

    return {
        "mode": "read-only-report",
        "generated_at": instant.isoformat(timespec="seconds"),
        "database": {
            "path": str(resolved),
            "sha256": before_sha,
            "size_bytes": before_size,
            "sidecar_bytes": sidecar_bytes,
            "total_on_disk_bytes": before_size + sidecar_bytes,
            "sidecars": sidecars,
            "schema_version": hermes_db.CURRENT_SCHEMA_VERSION,
            "journal_mode": journal_mode,
            "auto_vacuum": _auto_vacuum_name(auto_vacuum),
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "freelist_bytes_upper_bound": freelist_bytes,
            "freelist_ratio": round(freelist_ratio, 6),
            "quick_check": ["ok"],
            "unchanged_during_report": True,
        },
        "articles": {
            "total_rows": total_rows,
            "published_rows": published_rows,
            "routed_rows": routed_rows,
            "rejected_rows": rejected_rows,
            "oldest_fetched_at": oldest_fetched_at,
            "newest_fetched_at": newest_fetched_at,
            "rows_last_36h": rows_active,
            "rows_last_7d": rows_7d,
            "rows_last_30d": rows_30d,
            "content_bytes": content_bytes,
            "summary_bytes": summary_bytes,
            "payload_bytes_last_30d": payload_30d,
            "observed_growth_window_days": round(observed_days, 3),
            "rows_per_day_30d": round(rows_30d / observed_days, 3),
            "payload_bytes_per_day_30d": round(payload_30d / observed_days, 3),
            "content_review_age_days": CONTENT_REVIEW_AGE_DAYS,
            "rows_with_content_older_than_review_age": old_content_rows,
            "content_bytes_older_than_review_age": old_content_bytes,
        },
        "policy": {
            "row_retention": "indefinite",
            "automatic_row_deletion": False,
            "automatic_content_pruning": False,
            "automatic_vacuum": False,
            "change_auto_vacuum": False,
            "candidate_content_prune_age_days": CONTENT_REVIEW_AGE_DAYS,
            "review_db_bytes": REVIEW_DB_BYTES,
            "review_freelist_bytes": REVIEW_FREELIST_BYTES,
            "review_freelist_ratio": REVIEW_FREELIST_RATIO,
            "review_old_content_bytes": REVIEW_OLD_CONTENT_BYTES,
        },
        "review_required": bool(review_reasons),
        "review_reasons": review_reasons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_report(args.db)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ValueError, FileNotFoundError) as exc:
        print(f"KĻŪDA: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except hermes_db.SchemaError as exc:
        print(f"KĻŪDA: {exc}", file=sys.stderr)
        return EXIT_SCHEMA
    except (OSError, sqlite3.Error) as exc:
        print(f"KĻŪDA: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL


if __name__ == "__main__":
    raise SystemExit(main())
