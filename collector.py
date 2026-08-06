#!/usr/bin/env python3
"""Hermes Tech collector entrypoint with injectable runtime paths."""
from __future__ import annotations

from pathlib import Path
import sys

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

import collector_core as _core
from hermes_runtime import EXIT_USAGE, RuntimeConfigError, RuntimePaths


def _configure_core(paths: RuntimePaths) -> None:
    _core.BASE = paths.root
    _core.DB = paths.db
    _core.FEEDS = paths.feeds
    _core.LOG = paths.logs / "collector.log"


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
if hasattr(_core, "entry_published"):
    from hermes_time import install_collector_time_contracts

    install_collector_time_contracts(_core)
_export_core_api()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("Lietošana: collector.py", file=sys.stderr)
        return EXIT_USAGE
    return _core.main()


if __name__ == "__main__":
    raise SystemExit(main())
