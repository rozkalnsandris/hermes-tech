#!/usr/bin/env python3
"""Read-only preflight and evidence-producing SQLite migration apply tool."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
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
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


def _sidecars(db: Path) -> list[dict[str, Any]]:
    found = []
    for suffix in ("-journal", "-wal", "-shm"):
        path = Path(str(db) + suffix)
        if path.exists():
            found.append(
                {
                    "path": str(path.resolve()),
                    "size_bytes": path.stat().st_size,
                }
            )
    return found


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"evidence fails jau eksistē: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def build_preflight(db: Path) -> dict[str, Any]:
    resolved = db.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"DB nav parasts fails: {resolved}")
    report = hermes_db.preflight(resolved)
    sidecars = _sidecars(resolved)
    report.update(
        {
            "mode": "preflight",
            "current_schema_version": hermes_db.CURRENT_SCHEMA_VERSION,
            "sidecars": sidecars,
            "apply_safe": not sidecars,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return report


def apply_migration(
    db: Path,
    *,
    expected_sha256: str,
    backup_dir: Path,
    evidence_path: Path,
) -> dict[str, Any]:
    resolved = db.resolve(strict=True)
    if db.is_symlink() or not resolved.is_file():
        raise ValueError("apply nepieņem symlink vai ne-parastu DB failu")
    expected = expected_sha256.lower()
    if SHA256_RE.fullmatch(expected) is None:
        raise ValueError("--expected-sha256 jābūt 64 hex rakstzīmēm")
    sidecars = _sidecars(resolved)
    if sidecars:
        raise hermes_db.SchemaError(
            "apply bloķēts: atrasti SQLite sidecar faili; apturi writer "
            f"procesus un novāc droši pēc SQLite aizvēršanas: {sidecars}"
        )
    if evidence_path.exists():
        raise FileExistsError(f"evidence fails jau eksistē: {evidence_path}")

    before_report = build_preflight(resolved)
    if not before_report["apply_safe"]:
        raise hermes_db.SchemaError("preflight nav apply-safe")
    if before_report["journal_mode"] == "wal":
        raise hermes_db.SchemaError(
            "apply bloķēts WAL režīmā; apturi writer procesus un izveido "
            "checkpoint/rollback-journal stāvokli pirms apstiprinātā apply"
        )
    if before_report["sha256"] != expected:
        raise hermes_db.SchemaError(
            "DB SHA-256 nesakrīt ar apstiprināto vērtību: "
            f"expected={expected} actual={before_report['sha256']}"
        )

    backup_dir = backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / (
        f"{resolved.name}.before-schema-v{hermes_db.CURRENT_SCHEMA_VERSION}."
        f"{stamp}.{expected[:12]}.sqlite3"
    )
    if backup.exists():
        raise FileExistsError(f"backup jau eksistē: {backup}")

    applied_steps: tuple[str, ...] = ()
    backup_sha: str | None = None
    operation_started = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(resolved, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")

    def backup_under_lock(_conn: sqlite3.Connection) -> None:
        nonlocal backup_sha
        locked_sidecars = _sidecars(resolved)
        if locked_sidecars:
            raise hermes_db.SchemaError(
                f"SQLite sidecar parādījās pirms migrācijas: {locked_sidecars}"
            )
        actual = hermes_db.database_sha256(resolved)
        if actual != expected:
            raise hermes_db.SchemaError(
                "DB SHA-256 mainījās starp preflight un locked apply: "
                f"expected={expected} actual={actual}"
            )
        shutil.copy2(resolved, backup)
        os.chmod(backup, 0o600)
        backup_sha = hermes_db.database_sha256(backup)
        if backup_sha != expected:
            raise hermes_db.SchemaError(
                f"backup SHA-256 neatbilst avotam: {backup_sha} != {expected}"
            )
        backup_conn = hermes_db.open_readonly(backup)
        try:
            hermes_db.quick_check(backup_conn)
        finally:
            backup_conn.close()

    try:
        applied_steps = hermes_db.migrate_to_current(
            conn,
            before_migration=backup_under_lock,
        )
    except Exception as exc:
        failure = {
            "mode": "apply",
            "status": "failed",
            "started_at": operation_started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(resolved),
            "expected_sha256": expected,
            "before": before_report,
            "backup_path": str(backup) if backup.exists() else None,
            "backup_sha256": backup_sha,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _write_json_atomic(evidence_path, failure)
        raise
    finally:
        conn.close()

    after_report = build_preflight(resolved)
    if after_report["user_version"] != hermes_db.CURRENT_SCHEMA_VERSION:
        raise hermes_db.SchemaError("post-apply user_version nav current")
    if after_report["needs_change"]:
        raise hermes_db.SchemaError("post-apply joprojām rāda migrācijas plānu")

    evidence = {
        "mode": "apply",
        "status": "success",
        "started_at": operation_started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(resolved),
        "expected_sha256": expected,
        "before": before_report,
        "applied_steps": list(applied_steps),
        "backup_path": str(backup),
        "backup_sha256": backup_sha,
        "after": after_report,
    }
    _write_json_atomic(evidence_path, evidence)
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser(
        "preflight",
        help="read-only schema and integrity audit",
    )
    preflight.add_argument("--db", required=True, type=Path)
    preflight.add_argument("--evidence", type=Path)

    apply = sub.add_parser(
        "apply",
        help="locked, backed-up, evidence-producing upgrade",
    )
    apply.add_argument("--db", required=True, type=Path)
    apply.add_argument("--expected-sha256", required=True)
    apply.add_argument("--backup-dir", required=True, type=Path)
    apply.add_argument("--evidence", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            payload = build_preflight(args.db)
            if args.evidence is not None:
                _write_json_atomic(args.evidence, payload)
        else:
            payload = apply_migration(
                args.db,
                expected_sha256=args.expected_sha256,
                backup_dir=args.backup_dir,
                evidence_path=args.evidence,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ValueError, FileExistsError) as exc:
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
