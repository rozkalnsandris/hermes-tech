#!/usr/bin/env python3
"""Verify an isolated restored Hermes Tech tree without touching production.

The host backup owner is responsible for selecting/decrypting/extracting an
archive. This verifier starts *after* extraction and validates the restored
Hermes application root at ``<restore-root>/home/andris/hermes-tech``.

It reads restored secrets only as filesystem metadata: ``.env`` contents are
never opened or printed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any

APP_RELATIVE = Path("home/andris/hermes-tech")
PRODUCTION_APP = Path("/home/andris/hermes-tech")
EXPECTED_TABLES = {"articles", "sources"}
REQUIRED_PATHS = (
    Path(".git/HEAD"),
    Path(".env"),
    Path(".python-version"),
    Path("requirements.txt"),
    Path("collector.py"),
    Path("digest.py"),
    Path("publish.sh"),
    Path("run_digests.sh"),
    Path("tools/ci.sh"),
    Path("site/hugo.toml"),
    Path("data/hermes.db"),
)


class RestoreVerificationError(RuntimeError):
    """Raised when an isolated restore does not meet Hermes acceptance criteria."""


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path) -> str:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise RestoreVerificationError(
            f"command failed ({completed.returncode}): {command[0]}: {detail[:500]}"
        )
    return completed.stdout.strip()


def _resolve_app_root(restore_root: Path) -> tuple[Path, Path]:
    root = restore_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise RestoreVerificationError(f"restore root is not a directory: {root}")
    app = (root / APP_RELATIVE).resolve(strict=True)
    if not app.is_dir():
        raise RestoreVerificationError(f"restored Hermes root is not a directory: {app}")
    if app == PRODUCTION_APP.resolve(strict=False):
        raise RestoreVerificationError(
            "refusing to verify the live production Hermes root; restore into an isolated directory"
        )
    try:
        app.relative_to(root)
    except ValueError as exc:
        raise RestoreVerificationError(
            "restored Hermes path escapes the supplied restore root"
        ) from exc
    return root, app


def _check_required_paths(app: Path) -> list[str]:
    missing = [str(relative) for relative in REQUIRED_PATHS if not (app / relative).exists()]
    if missing:
        raise RestoreVerificationError(f"required restored paths are missing: {missing}")
    return [str(relative) for relative in REQUIRED_PATHS]


def _check_env_metadata(app: Path) -> dict[str, Any]:
    path = app / ".env"
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise RestoreVerificationError("restored .env is not a regular file")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise RestoreVerificationError(
            f"restored .env permissions are too broad: {mode:04o}; group/other bits must be zero"
        )
    return {
        "present": True,
        "size_bytes": int(info.st_size),
        "mode": f"{mode:04o}",
        "contents_read": False,
    }


def _check_git(app: Path) -> dict[str, Any]:
    safe = f"safe.directory={app}"
    inside = _run(["git", "-c", safe, "rev-parse", "--is-inside-work-tree"], cwd=app)
    if inside != "true":
        raise RestoreVerificationError("restored Git directory is not a work tree")
    head = _run(["git", "-c", safe, "rev-parse", "HEAD"], cwd=app)
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head.lower()):
        raise RestoreVerificationError(f"restored Git HEAD is invalid: {head!r}")
    _run(["git", "-c", safe, "fsck", "--no-dangling", "--no-reflogs"], cwd=app)
    return {"head": head, "fsck": "ok"}


def _check_sqlite(app: Path) -> dict[str, Any]:
    path = app / "data" / "hermes.db"
    before_sha = _file_sha256(path)
    before_size = path.stat().st_size
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA query_only = ON")
        quick = tuple(str(row[0]) for row in conn.execute("PRAGMA quick_check"))
        if quick != ("ok",):
            raise RestoreVerificationError(f"restored SQLite quick_check failed: {quick}")
        version_row = conn.execute("PRAGMA user_version").fetchone()
        if version_row is None:
            raise RestoreVerificationError("restored SQLite user_version is unavailable")
        user_version = int(version_row[0])
        if user_version < 1:
            raise RestoreVerificationError(
                f"restored SQLite schema is not versioned: user_version={user_version}"
            )
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not EXPECTED_TABLES.issubset(tables):
            raise RestoreVerificationError(
                f"restored SQLite is missing required tables: {sorted(EXPECTED_TABLES - tables)}"
            )
        article_count = int(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        source_count = int(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0])
    finally:
        conn.close()
    after_sha = _file_sha256(path)
    after_size = path.stat().st_size
    if (before_sha, before_size) != (after_sha, after_size):
        raise RestoreVerificationError("SQLite file changed during read-only restore verification")
    return {
        "sha256": before_sha,
        "size_bytes": int(before_size),
        "quick_check": "ok",
        "user_version": user_version,
        "article_count": article_count,
        "source_count": source_count,
        "unchanged_during_check": True,
    }


def _check_hugo(app: Path) -> dict[str, Any]:
    hugo = shutil.which("hugo")
    if hugo is None:
        raise RestoreVerificationError("hugo is not installed on the restore verification host")
    with tempfile.TemporaryDirectory(prefix="hermes-tech-restore-hugo-") as raw_tmp:
        temporary = Path(raw_tmp)
        destination = temporary / "public"
        cache = temporary / "cache"
        destination.mkdir()
        cache.mkdir()
        environment = os.environ.copy()
        environment["HUGO_CACHEDIR"] = str(cache)
        completed = subprocess.run(
            [
                hugo,
                "--source",
                str(app / "site"),
                "--destination",
                str(destination),
                "--cleanDestinationDir",
                "--noBuildLock",
                "--panicOnWarning",
                "--quiet",
            ],
            cwd=app,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise RestoreVerificationError(f"restored Hugo build failed: {detail[:500]}")
        index = destination / "index.html"
        if not index.is_file() or index.stat().st_size == 0:
            raise RestoreVerificationError("restored Hugo build did not produce index.html")
        sitemap = destination / "sitemap.xml"
        robots = destination / "robots.txt"
        if not sitemap.is_file() or not robots.is_file():
            raise RestoreVerificationError("restored Hugo build is missing sitemap.xml or robots.txt")
        return {
            "index_bytes": int(index.stat().st_size),
            "sitemap_bytes": int(sitemap.stat().st_size),
            "robots_bytes": int(robots.stat().st_size),
        }


def verify_restore(restore_root: Path) -> dict[str, Any]:
    root, app = _resolve_app_root(restore_root)
    required = _check_required_paths(app)
    env = _check_env_metadata(app)
    git = _check_git(app)
    sqlite_report = _check_sqlite(app)
    hugo = _check_hugo(app)
    return {
        "status": "pass",
        "mode": "isolated-restore-acceptance",
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "restore_root": str(root),
        "restored_app_root": str(app),
        "production_root_touched": False,
        "required_paths": required,
        "env": env,
        "git": git,
        "sqlite": sqlite_report,
        "hugo": hugo,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore-root", required=True, type=Path)
    parser.add_argument(
        "--evidence",
        type=Path,
        help="optional output JSON path outside the restored tree",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_restore(args.restore_root)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.evidence is not None:
            evidence = args.evidence.expanduser().resolve()
            restored = Path(report["restore_root"])
            try:
                evidence.relative_to(restored)
            except ValueError:
                pass
            else:
                raise RestoreVerificationError(
                    "evidence path must be outside the restored tree"
                )
            evidence.parent.mkdir(parents=True, exist_ok=True)
            if evidence.exists():
                raise RestoreVerificationError(f"evidence file already exists: {evidence}")
            fd = os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        sys.stdout.write(encoded)
        return 0
    except (OSError, sqlite3.Error, RestoreVerificationError) as exc:
        print(f"KĻŪDA: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
