#!/usr/bin/env python3
"""Canonical runtime path and exit-code contract for Hermes Tech."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

EXIT_OPERATIONAL = 1
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_LOCKED = 75


class RuntimeConfigError(ValueError):
    """Raised when the selected Hermes Tech runtime root is unsafe/invalid."""


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    db: Path
    logs: Path
    runs: Path
    digests: Path
    editorial: Path
    site: Path
    env_file: Path
    feeds: Path
    venv_python: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimePaths":
        values = os.environ if env is None else env
        raw = values.get("HERMES_TECH_ROOT", "").strip()
        root = Path(raw).expanduser() if raw else Path.home() / "hermes-tech"
        if not root.is_absolute():
            raise RuntimeConfigError(
                "HERMES_TECH_ROOT jābūt absolūtam ceļam"
            )
        root = root.resolve(strict=False)
        return cls(
            root=root,
            db=root / "data" / "hermes.db",
            logs=root / "logs",
            runs=root / "data" / "runs",
            digests=root / "digests",
            editorial=root / "editorial",
            site=root / "site",
            env_file=root / ".env",
            feeds=root / "feeds.txt",
            venv_python=root / "venv" / "bin" / "python",
        )
