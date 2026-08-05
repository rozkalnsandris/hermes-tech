#!/usr/bin/env python3
"""Hermes Tech digest CLI with injectable runtime paths and true dry-run.

Usage:
  digest.py classify
  digest.py digest <devops|ai|agents> [--dry-run]
  digest.py validate
  digest.py publish <devops|ai|agents> <YYYY-MM-DD>

Exit codes:
  0 success
  1 operational failure
  2 invalid CLI usage or runtime configuration
  3 validation failure
  75 lock contention (shell runners)
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from typing import Iterator

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

import digest_core as _core
from hermes_runtime import (
    EXIT_OPERATIONAL,
    EXIT_USAGE,
    EXIT_VALIDATION,
    RuntimeConfigError,
    RuntimePaths,
)


def _configure_core(paths: RuntimePaths) -> None:
    _core.BASE = paths.root
    _core.DB = paths.db
    _core.LOG = paths.logs / "digest.log"
    _core.RUNS = paths.runs
    _core.DIGESTS = paths.digests
    _core.ENV_FILE = paths.env_file


def _export_core_api() -> None:
    for name in dir(_core):
        if name.startswith("__") or name in globals():
            continue
        globals()[name] = getattr(_core, name)


try:
    PATHS = RuntimePaths.from_env()
except RuntimeConfigError as exc:
    print(f"KĻŪDA: {exc}", file=sys.stderr)
    raise SystemExit(EXIT_USAGE)

_configure_core(PATHS)
_export_core_api()


def print_usage() -> None:
    print(__doc__, file=sys.stderr)


def _api_key_required(step: str) -> bool:
    return step in {"classify", "digest"}


def _load_api_key(step: str) -> str | None:
    env = _core.load_env()
    api_key = env.get("DEEPSEEK_API_KEY", "")
    if _api_key_required(step) and not api_key:
        _core.log("KĻŪDA: DEEPSEEK_API_KEY nav .env — apstājos")
        return None
    return api_key


@contextmanager
def _dry_run_runtime(paths: RuntimePaths) -> Iterator[Path]:
    """Redirect every potentially mutating digest path to a temporary root."""
    if not paths.db.is_file():
        raise RuntimeError(f"nav datubāzes {paths.db}")

    original = (_core.DB, _core.LOG, _core.DIGESTS)
    with tempfile.TemporaryDirectory(prefix="hermes-tech-dry-run-") as raw_tmp:
        tmp = Path(raw_tmp)
        tmp_db = tmp / "data" / "hermes.db"
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(
            f"file:{paths.db}?mode=ro", uri=True, timeout=30
        )
        target = sqlite3.connect(tmp_db)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        _core.DB = tmp_db
        _core.LOG = tmp / "logs" / "digest.log"
        _core.DIGESTS = tmp / "digests"
        try:
            yield tmp
        finally:
            _core.DB, _core.LOG, _core.DIGESTS = original


def _run_digest_dry(api_key: str, category: str) -> int:
    try:
        with _dry_run_runtime(PATHS) as tmp:
            rc = _core.step_digest(api_key, category, dry_run=True)
            if rc != 0:
                return EXIT_OPERATIONAL
            outputs = sorted((tmp / "digests").glob(f"*-{category}.md"))
            if len(outputs) != 1:
                raise RuntimeError(
                    "dry-run neizveidoja tieši vienu pagaidu digesta failu"
                )
            print("--- HERMES DRY-RUN OUTPUT BEGIN ---")
            print(outputs[0].read_text(encoding="utf-8"), end="")
            print("--- HERMES DRY-RUN OUTPUT END ---")
            return 0
    except (OSError, RuntimeError) as exc:
        print(f"KĻŪDA: dry-run neizdevās: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print_usage()
        return EXIT_USAGE

    step = args[0]
    categories = set(_core.CATS)

    if step == "classify":
        if len(args) != 1:
            print_usage()
            return EXIT_USAGE
        api_key = _load_api_key(step)
        return EXIT_OPERATIONAL if api_key is None else _core.step_classify(api_key)

    if step == "digest":
        if len(args) not in (2, 3) or args[1] not in categories:
            print_usage()
            return EXIT_USAGE
        dry_run = len(args) == 3
        if dry_run and args[2] != "--dry-run":
            print_usage()
            return EXIT_USAGE
        api_key = _load_api_key(step)
        if api_key is None:
            return EXIT_OPERATIONAL
        if dry_run:
            return _run_digest_dry(api_key, args[1])
        return _core.step_digest(api_key, args[1], dry_run=False)

    if step == "validate":
        if len(args) != 1:
            print_usage()
            return EXIT_USAGE
        return 0 if _core.step_validate("") == 0 else EXIT_VALIDATION

    if step == "publish":
        if (
            len(args) != 3
            or args[1] not in categories
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", args[2]) is None
        ):
            print_usage()
            return EXIT_USAGE
        return _core.step_publish("", args[1], args[2])

    print(f"KĻŪDA: nezināms step '{step}'", file=sys.stderr)
    print_usage()
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
