#!/usr/bin/env python3
"""Pending-digest reservation and cross-draft integrity contracts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Callable, Iterable

MAX_PENDING_DIGEST_DATES = 31
EXIT_VALIDATION = 3
_DRAFT_RE = re.compile(
    r"^digests/([0-9]{4}-[0-9]{2}-[0-9]{2})(?:-(devops|ai|agents))?\.md$"
)
_SELECTED_IDS_RE = re.compile(
    r"^<!--\s*selected_ids:\s*([0-9]+(?:,[0-9]+)*)\s*-->$"
)
_CATEGORY_ORDER = {"devops": 0, "ai": 1, "agents": 2}


class PendingDigestError(RuntimeError):
    """Pending digest state cannot be interpreted safely."""


@dataclass(frozen=True)
class PendingDraft:
    relative_path: str
    digest_date: str
    category: str
    selected_ids: tuple[int, ...]


@dataclass(frozen=True)
class PendingState:
    drafts: tuple[PendingDraft, ...]
    id_owners: dict[int, frozenset[str]]
    topic_owners: dict[tuple[str, str], frozenset[str]]
    topic_by_id: dict[int, str]


def _run_git(root: Path, *args: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PendingDigestError(f"git pārbaude neizdevās: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise PendingDigestError(detail or "git pārbaude neizdevās")
    return proc.stdout


def _git_paths(root: Path, *args: str) -> list[str]:
    raw = _run_git(root, *args, "-z", "--", "digests")
    return [
        item.decode("utf-8", errors="strict")
        for item in raw.split(b"\0")
        if item
    ]


def _parse_draft_path(relative_path: str) -> tuple[str, str]:
    match = _DRAFT_RE.fullmatch(relative_path)
    if match is None:
        raise PendingDigestError(
            f"neatļauts pending digest ceļš: {relative_path}"
        )
    digest_date = match.group(1)
    category = match.group(2) or "devops"
    return digest_date, category


def _parse_selected_ids(path: Path, relative_path: str) -> tuple[int, ...]:
    if not path.is_file() or path.is_symlink():
        raise PendingDigestError(
            f"pending digest nav drošs parasts fails: {relative_path}"
        )
    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline().rstrip("\r\n")
    except OSError as exc:
        raise PendingDigestError(
            f"nevar nolasīt pending digest {relative_path}: {exc}"
        ) from exc

    match = _SELECTED_IDS_RE.fullmatch(first_line)
    if match is None:
        raise PendingDigestError(
            f"pending digest nav derīgas selected_ids rindas: {relative_path}"
        )

    selected_ids = tuple(int(raw) for raw in match.group(1).split(","))
    if not selected_ids or any(value <= 0 for value in selected_ids):
        raise PendingDigestError(
            f"pending digest satur nederīgus selected_ids: {relative_path}"
        )
    if len(set(selected_ids)) != len(selected_ids):
        raise PendingDigestError(
            f"pending digest satur dublētus selected_ids: {relative_path}"
        )
    return selected_ids


def collect_pending_drafts(root: Path) -> tuple[PendingDraft, ...]:
    """Read pending digest drafts from the Git working tree, without mutation."""

    root = root.resolve()
    try:
        top_level = Path(
            _run_git(root, "rev-parse", "--show-toplevel")
            .decode("utf-8")
            .strip()
        ).resolve()
    except (UnicodeDecodeError, OSError) as exc:
        raise PendingDigestError(f"nevar noteikt Git sakni: {exc}") from exc

    if top_level != root:
        raise PendingDigestError(
            f"HERMES_TECH_ROOT nav Git top-level: root={root} git={top_level}"
        )

    staged = _git_paths(
        root,
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
    )
    if staged:
        raise PendingDigestError(
            "pending digest preflight laikā ir staged izmaiņas: "
            + ",".join(sorted(staged))
        )

    unstaged = _git_paths(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
    )
    untracked = _git_paths(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
    )
    paths = sorted(set(unstaged) | set(untracked))

    drafts: list[PendingDraft] = []
    seen_slot: dict[tuple[str, str], str] = {}
    dates: set[str] = set()

    for relative_path in paths:
        digest_date, category = _parse_draft_path(relative_path)
        slot = (digest_date, category)
        previous = seen_slot.get(slot)
        if previous is not None:
            raise PendingDigestError(
                "vairāki pending drafti vienai kategorijai/datumam: "
                f"{previous},{relative_path}"
            )
        seen_slot[slot] = relative_path
        dates.add(digest_date)
        selected_ids = _parse_selected_ids(root / relative_path, relative_path)
        drafts.append(
            PendingDraft(
                relative_path=relative_path,
                digest_date=digest_date,
                category=category,
                selected_ids=selected_ids,
            )
        )

    if len(dates) > MAX_PENDING_DIGEST_DATES:
        raise PendingDigestError(
            "pending digest backlog aptver pārāk daudz datumus: "
            f"{len(dates)} > {MAX_PENDING_DIGEST_DATES}"
        )

    drafts.sort(
        key=lambda draft: (
            draft.digest_date,
            _CATEGORY_ORDER[draft.category],
            draft.relative_path,
        )
    )
    return tuple(drafts)


def load_pending_state(
    conn: sqlite3.Connection,
    drafts: Iterable[PendingDraft],
) -> PendingState:
    """Resolve pending selected IDs to canonical DB category/topic state."""

    materialized = tuple(drafts)
    all_ids = sorted(
        {
            article_id
            for draft in materialized
            for article_id in draft.selected_ids
        }
    )
    if not all_ids:
        return PendingState(materialized, {}, {}, {})

    placeholders = ",".join("?" for _ in all_ids)
    rows = conn.execute(
        f"""
        SELECT id, primary_category, digest_date, topic_key
        FROM articles
        WHERE id IN ({placeholders})
        """,
        all_ids,
    ).fetchall()
    by_id = {int(row[0]): row for row in rows}

    missing = [article_id for article_id in all_ids if article_id not in by_id]
    if missing:
        raise PendingDigestError(
            "DB trūkst pending selected article ID: "
            + ",".join(map(str, missing))
        )

    id_owners_mut: dict[int, set[str]] = defaultdict(set)
    topic_owners_mut: dict[tuple[str, str], set[str]] = defaultdict(set)
    topic_by_id: dict[int, str] = {}

    for draft in materialized:
        for article_id in draft.selected_ids:
            row = by_id[article_id]
            db_category = row[1]
            digest_date = row[2]
            topic_key = row[3]

            if db_category != draft.category:
                raise PendingDigestError(
                    f"pending article {article_id} kategorija neatbilst "
                    f"{draft.relative_path}: db={db_category!r} "
                    f"draft={draft.category!r}"
                )
            if digest_date not in (None, draft.digest_date):
                raise PendingDigestError(
                    f"pending article {article_id} jau piesaistīts datumam "
                    f"{digest_date!r}, draft={draft.digest_date}"
                )
            if not isinstance(topic_key, str) or not topic_key.strip():
                raise PendingDigestError(
                    f"pending article {article_id} trūkst topic_key"
                )

            topic_key = topic_key.strip()
            topic_by_id[article_id] = topic_key
            id_owners_mut[article_id].add(draft.relative_path)
            topic_owners_mut[(draft.category, topic_key)].add(
                draft.relative_path
            )

    return PendingState(
        drafts=materialized,
        id_owners={
            key: frozenset(value)
            for key, value in id_owners_mut.items()
        },
        topic_owners={
            key: frozenset(value)
            for key, value in topic_owners_mut.items()
        },
        topic_by_id=topic_by_id,
    )


def _current_draft_paths(category: str, digest_date: str) -> set[str]:
    paths = {f"digests/{digest_date}-{category}.md"}
    if category == "devops":
        paths.add(f"digests/{digest_date}.md")
    return paths


def filter_reserved_candidates(
    *,
    root: Path,
    conn: sqlite3.Connection,
    candidates: list[dict],
    category: str,
    digest_date: str,
    logger: Callable[[str], None] | None = None,
) -> list[dict]:
    """Exclude IDs/topics already owned by another pending digest draft."""

    drafts = collect_pending_drafts(root)
    if not drafts:
        return candidates

    state = load_pending_state(conn, drafts)
    current_paths = _current_draft_paths(category, digest_date)
    kept: list[dict] = []
    blocked_ids: list[int] = []

    for candidate in candidates:
        article_id = candidate.get("id")
        topic_key = candidate.get("topic_key")
        if not isinstance(article_id, int) or isinstance(article_id, bool):
            raise PendingDigestError(
                f"kandidātam ir nederīgs article id: {article_id!r}"
            )
        if not isinstance(topic_key, str) or not topic_key.strip():
            raise PendingDigestError(
                f"kandidātam {article_id} trūkst topic_key"
            )
        topic_key = topic_key.strip()

        id_owners = set(state.id_owners.get(article_id, frozenset()))
        topic_owners = set(
            state.topic_owners.get((category, topic_key), frozenset())
        )
        id_owners.difference_update(current_paths)
        topic_owners.difference_update(current_paths)

        if id_owners or topic_owners:
            blocked_ids.append(article_id)
            continue
        kept.append(candidate)

    if blocked_ids and logger is not None:
        logger(
            f"[{category}] Pending draft reservation izslēdza "
            f"{len(blocked_ids)} kandidātu(s): "
            + ",".join(map(str, blocked_ids))
        )
    return kept


def validate_pending_drafts(
    root: Path,
    conn: sqlite3.Connection,
) -> PendingState:
    """Fail closed on cross-draft selected-ID or same-category topic reuse."""

    drafts = collect_pending_drafts(root)
    state = load_pending_state(conn, drafts)
    if not drafts:
        return state

    by_path = {draft.relative_path: draft for draft in drafts}
    errors: list[str] = []

    for article_id, owners in sorted(state.id_owners.items()):
        if len(owners) > 1:
            errors.append(
                f"article_id {article_id} ir vairākos pending draftos: "
                + ",".join(sorted(owners))
            )

    for (category, topic_key), owners in sorted(state.topic_owners.items()):
        dates = {by_path[path].digest_date for path in owners}
        if len(dates) > 1:
            errors.append(
                f"topic_key {category}/{topic_key} ir vairākos datumos: "
                + ",".join(sorted(owners))
            )

    if errors:
        raise PendingDigestError("; ".join(errors))
    return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--root", required=True, type=Path)
    validate.add_argument("--db", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command != "validate":
        return 2

    root = args.root.resolve()
    db = args.db.resolve()
    if not root.is_dir():
        print(
            f"KĻŪDA: PENDING_DIGEST_PREFLIGHT: nav root {root}",
            file=sys.stderr,
        )
        return EXIT_VALIDATION
    if not db.is_file() or db.is_symlink():
        print(
            f"KĻŪDA: PENDING_DIGEST_PREFLIGHT: nedrošs DB ceļš {db}",
            file=sys.stderr,
        )
        return EXIT_VALIDATION

    try:
        uri = db.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
        try:
            state = validate_pending_drafts(root, conn)
        finally:
            conn.close()
    except (OSError, sqlite3.Error, PendingDigestError) as exc:
        print(
            f"KĻŪDA: PENDING_DIGEST_PREFLIGHT: {exc}",
            file=sys.stderr,
        )
        return EXIT_VALIDATION

    unique_ids = len(state.id_owners)
    dates = len({draft.digest_date for draft in state.drafts})
    print(
        "PENDING_DIGEST_PREFLIGHT=PASS "
        f"drafts={len(state.drafts)} dates={dates} "
        f"selected_ids={unique_ids}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
