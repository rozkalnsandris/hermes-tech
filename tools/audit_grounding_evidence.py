#!/usr/bin/env python3
"""Measure Hermes digest grounding depth without exposing article text.

The audit reads an existing Hermes runtime root, parses selected article IDs from
recent digest source files, and measures how much feed-provided content exists
beyond the 300-character evidence currently sent to digest generation.

No summary/content text, URLs, secrets or prompts are emitted. The SQLite file
is opened read-only and its hash/size must remain unchanged during the audit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import statistics
import sys
from typing import Any

SELECTED_RE = re.compile(r"<!--\s*selected_ids:\s*([0-9,\s]+)\s*-->")
DIGEST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(devops|ai|agents)\.md$")
CURRENT_PROMPT_CHARS = 300
REVIEW_EXCERPT_CHARS = 1200
DEFAULT_DAYS = 30
DEFAULT_MAX_DIGESTS = 90


class GroundingAuditError(RuntimeError):
    """Raised when read-only grounding evidence cannot be trusted."""


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_ids(path: Path) -> list[int]:
    text = path.read_text(encoding="utf-8")
    match = SELECTED_RE.search(text)
    if match is None:
        raise GroundingAuditError(f"selected_ids metadata missing: {path.name}")
    values = [int(value.strip()) for value in match.group(1).split(",") if value.strip()]
    if not values or len(values) != len(set(values)):
        raise GroundingAuditError(f"selected_ids invalid or duplicated: {path.name}")
    return values


def recent_digest_files(
    digests: Path,
    *,
    now: datetime,
    days: int,
    max_digests: int,
) -> list[Path]:
    if days < 1:
        raise ValueError("days must be >= 1")
    if max_digests < 1:
        raise ValueError("max_digests must be >= 1")
    cutoff = now.date() - timedelta(days=days - 1)
    found: list[tuple[date, str, Path]] = []
    for path in digests.glob("*.md"):
        match = DIGEST_RE.fullmatch(path.name)
        if match is None:
            continue
        digest_date = date.fromisoformat(match.group(1))
        if digest_date < cutoff or digest_date > now.date():
            continue
        found.append((digest_date, match.group(2), path))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in found[:max_digests]]


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def build_report(
    root: Path,
    *,
    days: int = DEFAULT_DAYS,
    max_digests: int = DEFAULT_MAX_DIGESTS,
    now: datetime | None = None,
) -> dict[str, Any]:
    runtime = root.expanduser().resolve(strict=True)
    db = runtime / "data" / "hermes.db"
    digests = runtime / "digests"
    if not db.is_file():
        raise GroundingAuditError(f"database missing: {db}")
    if not digests.is_dir():
        raise GroundingAuditError(f"digests directory missing: {digests}")

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    instant = instant.astimezone(timezone.utc)

    digest_files = recent_digest_files(
        digests,
        now=instant,
        days=days,
        max_digests=max_digests,
    )
    if not digest_files:
        raise GroundingAuditError("no digest source files found in requested audit window")

    requested_ids: list[int] = []
    id_occurrences: Counter[int] = Counter()
    by_category: Counter[str] = Counter()
    for path in digest_files:
        match = DIGEST_RE.fullmatch(path.name)
        assert match is not None
        ids = selected_ids(path)
        by_category[match.group(2)] += len(ids)
        requested_ids.extend(ids)
        id_occurrences.update(ids)

    duplicate_across_digests = sorted(
        article_id for article_id, count in id_occurrences.items() if count > 1
    )
    unique_ids = sorted(set(requested_ids))

    before_sha = file_sha256(db)
    before_size = db.stat().st_size
    conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA query_only = ON")
        quick = tuple(str(row[0]) for row in conn.execute("PRAGMA quick_check"))
        if quick != ("ok",):
            raise GroundingAuditError(f"SQLite quick_check failed: {quick}")

        records: dict[int, tuple[str, str, str | None, int, int]] = {}
        batch_size = 400
        for start in range(0, len(unique_ids), batch_size):
            batch = unique_ids[start:start + batch_size]
            placeholders = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"""SELECT id, source, fetched_at, digest_date,
                           COALESCE(length(summary), 0),
                           COALESCE(length(content), 0)
                    FROM articles
                    WHERE id IN ({placeholders})""",
                batch,
            ).fetchall()
            for row in rows:
                records[int(row[0])] = (
                    str(row[1]),
                    str(row[2]),
                    str(row[3]) if row[3] is not None else None,
                    int(row[4]),
                    int(row[5]),
                )
    finally:
        conn.close()

    after_sha = file_sha256(db)
    after_size = db.stat().st_size
    if (before_sha, before_size) != (after_sha, after_size):
        raise GroundingAuditError("database changed during read-only grounding audit")

    missing_ids = sorted(set(unique_ids) - set(records))
    content_lengths: list[int] = []
    summary_lengths: list[int] = []
    extra_beyond_300: list[int] = []
    proposed_additional_chars: list[int] = []
    sources: Counter[str] = Counter()
    digest_date_mismatches: list[int] = []

    for article_id in unique_ids:
        record = records.get(article_id)
        if record is None:
            continue
        source, _fetched_at, digest_date, summary_len, content_len = record
        sources[source] += 1
        summary_lengths.append(summary_len)
        content_lengths.append(content_len)
        extra_beyond_300.append(max(0, content_len - CURRENT_PROMPT_CHARS))
        proposed_additional_chars.append(
            max(
                0,
                min(content_len, REVIEW_EXCERPT_CHARS)
                - min(content_len, CURRENT_PROMPT_CHARS),
            )
        )
        if digest_date is None:
            digest_date_mismatches.append(article_id)

    found_count = len(records)
    with_extra = sum(length > CURRENT_PROMPT_CHARS for length in content_lengths)
    with_1200_plus = sum(length > REVIEW_EXCERPT_CHARS for length in content_lengths)
    total_proposed_extra_chars = sum(proposed_additional_chars)

    return {
        "status": "pass" if not missing_ids else "incomplete",
        "mode": "read-only-grounding-evidence-audit",
        "generated_at": instant.isoformat(timespec="seconds"),
        "runtime_root": str(runtime),
        "window_days": days,
        "max_digests": max_digests,
        "digest_files_sampled": len(digest_files),
        "selected_id_references": len(requested_ids),
        "unique_selected_ids": len(unique_ids),
        "duplicate_selected_ids_across_digests": duplicate_across_digests,
        "selected_ids_by_category": dict(sorted(by_category.items())),
        "database": {
            "sha256": before_sha,
            "size_bytes": int(before_size),
            "quick_check": "ok",
            "unchanged_during_audit": True,
        },
        "rows": {
            "found": found_count,
            "missing_ids": missing_ids,
            "selected_rows_without_digest_date": sorted(digest_date_mismatches),
            "distinct_sources": len(sources),
            "source_counts": dict(sorted(sources.items())),
        },
        "evidence_depth": {
            "current_digest_prompt_content_chars": CURRENT_PROMPT_CHARS,
            "review_candidate_excerpt_chars": REVIEW_EXCERPT_CHARS,
            "rows_with_content_beyond_300": with_extra,
            "rows_with_content_beyond_1200": with_1200_plus,
            "share_with_content_beyond_300": (
                round(with_extra / found_count, 4) if found_count else 0.0
            ),
            "summary_chars_median": int(statistics.median(summary_lengths)) if summary_lengths else 0,
            "content_chars_median": int(statistics.median(content_lengths)) if content_lengths else 0,
            "content_chars_p90": percentile(content_lengths, 0.90),
            "content_chars_max": max(content_lengths, default=0),
            "total_chars_beyond_300_available": sum(extra_beyond_300),
            "total_additional_chars_if_excerpt_raised_to_1200": total_proposed_extra_chars,
            "approx_additional_english_input_tokens_at_1200": round(
                total_proposed_extra_chars * 0.3
            ),
        },
        "privacy": {
            "article_text_emitted": False,
            "article_urls_emitted": False,
            "secrets_read": False,
            "model_or_network_call_performed": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--max-digests", type=int, default=DEFAULT_MAX_DIGESTS)
    parser.add_argument("--evidence", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_report(
            args.root,
            days=args.days,
            max_digests=args.max_digests,
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.evidence is not None:
            evidence = args.evidence.expanduser().resolve()
            if evidence.exists():
                raise GroundingAuditError(f"evidence file already exists: {evidence}")
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(encoded, encoding="utf-8")
        sys.stdout.write(encoded)
        return 0 if report["status"] == "pass" else 3
    except (OSError, sqlite3.Error, ValueError, GroundingAuditError) as exc:
        print(f"KĻŪDA: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
