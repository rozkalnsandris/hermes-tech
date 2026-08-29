#!/usr/bin/env python3
"""Bounded feed-evidence contract for Hermes Tech digest candidates.

The production DB already stores feed ``content`` for routed articles. The
legacy digest candidate loader exposed only ``summary[:300]`` plus the full
content length, which left most selected stories grounded on a much narrower
slice than the stored feed evidence actually available.

This module replaces only the digest candidate read path. It keeps the existing
summary field, adds a bounded 1,200-character feed-content excerpt, and applies
the pending-draft reservation contract before candidates reach diversity/model
selection. Classification, model/provider, database schema and publication
semantics remain unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from digest_pending import filter_reserved_candidates

DIGEST_EVIDENCE_EXCERPT_CHARS = 1200
DIGEST_SUMMARY_CHARS = 300


def install_grounding_evidence_contracts(core: Any) -> None:
    """Install the bounded evidence loader plus pending-draft reservations."""
    if getattr(core, "_hermes_grounding_evidence_v1", False):
        return
    if not hasattr(core, "fetch_routed_candidates"):
        return

    def fetch_routed_candidates(conn: Any, category: str) -> list[dict]:
        rows = conn.execute(
            """SELECT id, source, title, link, summary, topic_key, content
               FROM articles
               WHERE primary_category = ?
                 AND topic_key IS NOT NULL
                 AND digest_date IS NULL
                 AND fetched_at >= datetime('now', ?)
               ORDER BY id DESC""",
            (category, f"-{int(getattr(core, 'FETCH_HOURS', 36))} hours"),
        ).fetchall()

        candidates: list[dict] = []
        for row in rows:
            content = row[6] or ""
            candidates.append(
                {
                    "id": row[0],
                    "source": row[1],
                    "title": row[2],
                    "link": row[3],
                    "summary": (row[4] or "")[:DIGEST_SUMMARY_CHARS],
                    "topic_key": row[5] or "",
                    "content_excerpt": content[:DIGEST_EVIDENCE_EXCERPT_CHARS],
                    "content_length": len(content),
                }
            )

        # Minimal fake cores in isolated unit tests intentionally omit BASE.
        # Real Hermes runtime always supplies BASE; there, any Git/metadata
        # ambiguity fails closed before model selection.
        raw_root = getattr(core, "BASE", None)
        if raw_root is None:
            return candidates

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return filter_reserved_candidates(
            root=Path(raw_root),
            conn=conn,
            candidates=candidates,
            category=category,
            digest_date=today,
            logger=getattr(core, "log", None),
        )

    core.fetch_routed_candidates = fetch_routed_candidates
    core.DIGEST_EVIDENCE_EXCERPT_CHARS = DIGEST_EVIDENCE_EXCERPT_CHARS
    core._hermes_grounding_evidence_v1 = True
