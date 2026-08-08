#!/usr/bin/env python3
"""Validate Hermes Tech's pinned toolchain declarations without dependencies."""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s;]+)$")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def exact_pin(value: str, source: str) -> tuple[str, str]:
    match = PIN_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"{source}: dependency is not an exact == pin: {value!r}")
    return canonical_name(match.group("name")), match.group("version")


def read_lock(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, version = exact_pin(line, f"{path.name}:{number}")
        if name in pins:
            raise ValueError(f"{path.name}:{number}: duplicate dependency {name}")
        pins[name] = version
    if not pins:
        raise ValueError(f"{path.name}: no dependency pins found")
    return pins


def main() -> int:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    hermes = data["tool"]["hermes"]

    python_file = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    if python_file != hermes["python"]:
        raise ValueError(
            f"Python drift: .python-version={python_file!r}, "
            f"pyproject tool.hermes.python={hermes['python']!r}"
        )
    if project.get("requires-python") != ">=3.11,<3.12":
        raise ValueError("pyproject requires-python must remain >=3.11,<3.12")
    if hermes.get("hugo") != "0.164.0":
        raise ValueError("supported Hugo version must remain exactly 0.164.0")

    bootstrap = read_lock(ROOT / "requirements-bootstrap.txt")
    expected_bootstrap = {
        "pip": str(hermes.get("pip", "")),
        "setuptools": str(hermes.get("setuptools", "")),
    }
    if bootstrap != expected_bootstrap:
        raise ValueError(
            "requirements-bootstrap.txt must exactly match tool.hermes "
            f"pip/setuptools pins: expected={expected_bootstrap}, actual={bootstrap}"
        )

    runtime = read_lock(ROOT / "requirements.txt")
    direct: dict[str, str] = {}
    for dependency in project.get("dependencies", []):
        name, version = exact_pin(dependency, "pyproject.toml")
        direct[name] = version

    missing = {
        name: version
        for name, version in direct.items()
        if runtime.get(name) != version
    }
    if missing:
        raise ValueError(f"requirements.txt is out of sync with pyproject: {missing}")

    dev_lines = [
        line.strip()
        for line in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if dev_lines != ["-r requirements.txt"]:
        raise ValueError(
            "requirements-dev.txt must inherit the exact runtime lock with "
            "'-r requirements.txt'"
        )

    print(
        f"Dependency contract OK: Python {python_file}, Hugo {hermes['hugo']}, "
        f"{len(bootstrap) + len(runtime)} exact Python pins"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"KĻŪDA: dependency contract: {exc}", file=sys.stderr)
        raise SystemExit(1)
