#!/usr/bin/env python3
"""Hermes Tech OG-card entrypoint with injectable runtime paths."""
from __future__ import annotations

from pathlib import Path
import sys

_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

import ogcard_core as _core
from hermes_runtime import EXIT_USAGE, RuntimeConfigError, RuntimePaths


def _configure_core(paths: RuntimePaths) -> None:
    _core.BASE = paths.root
    _core.STATIC = paths.site / "static"
    _core.WORDMARK = _core.STATIC / "brand" / "hermes-tech-wordmark-v15.png"
    _core.MARK = _core.STATIC / "brand" / "hermes-tech-mark-v15.png"
    _core.OUT_DIR = _core.STATIC / "og"


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


def main(argv: list[str] | None = None) -> int:
    if argv is not None:
        old = sys.argv
        sys.argv = [old[0], *argv]
        try:
            return _core.main()
        finally:
            sys.argv = old
    return _core.main()


if __name__ == "__main__":
    raise SystemExit(main())
