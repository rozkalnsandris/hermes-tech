#!/usr/bin/env python3
"""Classify an exact Git diff by Hermes Tech production rollout impact.

This module is intentionally repository-only and secret-free.  It provides one
canonical path policy for CI and later pull-deploy/manual-rollout consumers.
Unknown production-adjacent paths fail toward AUTO_DEPLOY_SAFE rather than being
silently treated as no-deploy work.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys
from typing import Iterable

NO_DEPLOY = "NO_DEPLOY"
AUTO_DEPLOY_SAFE = "AUTO_DEPLOY_SAFE"
CONTROL_PLANE_APPROVAL_REQUIRED = "CONTROL_PLANE_APPROVAL_REQUIRED"
RUNTIME_ROLLOUT_REQUIRED = "RUNTIME_ROLLOUT_REQUIRED"
DB_APPLY_REQUIRES_SEPARATE_APPROVAL = "DB_APPLY_REQUIRES_SEPARATE_APPROVAL"

_REQUIREMENTS_RE = re.compile(r"^requirements(?:-[A-Za-z0-9_.-]+)?\.txt$")


@dataclass(frozen=True)
class DeployImpact:
    classification: str
    deploy_required: bool
    control_plane_changed: bool
    runtime_changed: bool
    db_sensitive_changed: bool
    changed_paths: tuple[str, ...]
    control_plane_paths: tuple[str, ...]
    runtime_paths: tuple[str, ...]
    db_sensitive_paths: tuple[str, ...]
    no_deploy_paths: tuple[str, ...]
    auto_deploy_paths: tuple[str, ...]

    def as_dict(self, *, base_sha: str = "", target_sha: str = "") -> dict[str, object]:
        return {
            "base_sha": base_sha,
            "target_sha": target_sha,
            "classification": self.classification,
            "deploy_required": self.deploy_required,
            "control_plane_changed": self.control_plane_changed,
            "runtime_changed": self.runtime_changed,
            "db_sensitive_changed": self.db_sensitive_changed,
            "changed_paths": list(self.changed_paths),
            "control_plane_paths": list(self.control_plane_paths),
            "runtime_paths": list(self.runtime_paths),
            "db_sensitive_paths": list(self.db_sensitive_paths),
            "no_deploy_paths": list(self.no_deploy_paths),
            "auto_deploy_paths": list(self.auto_deploy_paths),
        }


def _normalize_path(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("empty changed path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe changed path: {raw!r}")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"invalid changed path: {raw!r}")
    return normalized


def _is_control_plane(path: str) -> bool:
    return (
        path.startswith(".github/workflows/")
        or path.startswith("tools/pull-deploy/")
        or path in {"tools/ci.sh", "tools/classify_deploy_impact.py"}
        or path
        in {
            "ops/systemd/hermes-tech-pull-deploy.service",
            "ops/systemd/hermes-tech-pull-deploy.timer",
        }
    )


def _is_runtime_sensitive(path: str) -> bool:
    return (
        path in {".python-version", "pyproject.toml", "tools/install_hugo.sh"}
        or bool(_REQUIREMENTS_RE.fullmatch(path))
        or path.startswith("tools/install_python")
        or path.startswith("tools/runtime/")
    )


def _is_db_sensitive(path: str) -> bool:
    return (
        path in {"hermes_db.py", "tools/sqlite_schema.py"}
        or path.startswith("migrations/")
        or path.startswith("db/migrations/")
        or path.startswith("schema/")
    )


def _is_no_deploy(path: str) -> bool:
    if path.startswith("docs/") or path.startswith("tests/"):
        return True
    if path.startswith(".github/") and not path.startswith(".github/workflows/"):
        return True
    return path in {
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
    }


def classify_paths(paths: Iterable[str]) -> DeployImpact:
    changed = tuple(sorted({_normalize_path(raw) for raw in paths}))

    control = tuple(path for path in changed if _is_control_plane(path))
    runtime = tuple(path for path in changed if _is_runtime_sensitive(path))
    db_sensitive = tuple(path for path in changed if _is_db_sensitive(path))

    stronger = set(control) | set(runtime) | set(db_sensitive)
    no_deploy = tuple(
        path for path in changed if path not in stronger and _is_no_deploy(path)
    )
    auto = tuple(
        path for path in changed if path not in stronger and path not in set(no_deploy)
    )

    if db_sensitive:
        classification = DB_APPLY_REQUIRES_SEPARATE_APPROVAL
    elif runtime:
        classification = RUNTIME_ROLLOUT_REQUIRED
    elif control:
        classification = CONTROL_PLANE_APPROVAL_REQUIRED
    elif auto:
        classification = AUTO_DEPLOY_SAFE
    else:
        classification = NO_DEPLOY

    return DeployImpact(
        classification=classification,
        deploy_required=classification != NO_DEPLOY,
        control_plane_changed=bool(control),
        runtime_changed=bool(runtime),
        db_sensitive_changed=bool(db_sensitive),
        changed_paths=changed,
        control_plane_paths=control,
        runtime_paths=runtime,
        db_sensitive_paths=db_sensitive,
        no_deploy_paths=no_deploy,
        auto_deploy_paths=auto,
    )


def _resolve_commit(revision: str) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "unknown git error"
        raise RuntimeError(f"cannot resolve commit {revision!r}: {detail}")
    sha = proc.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise RuntimeError(f"git returned invalid SHA for {revision!r}: {sha!r}")
    return sha


def changed_paths_between(base: str, target: str) -> tuple[str, str, tuple[str, ...]]:
    base_sha = _resolve_commit(base)
    target_sha = _resolve_commit(target)
    proc = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACDMRTUXB",
            f"{base_sha}..{target_sha}",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or "unknown git error"
        raise RuntimeError(f"git diff failed: {detail}")
    paths = tuple(line for line in proc.stdout.splitlines() if line.strip())
    return base_sha, target_sha, paths


def _default_base(target: str) -> str:
    return f"{target}^"


def _print_env(impact: DeployImpact, *, base_sha: str, target_sha: str) -> None:
    print(f"HERMES_TECH_DEPLOY_IMPACT={impact.classification}")
    print(f"HERMES_TECH_DEPLOY_REQUIRED={'yes' if impact.deploy_required else 'no'}")
    print(f"CONTROL_PLANE_CHANGED={'true' if impact.control_plane_changed else 'false'}")
    print(f"RUNTIME_CHANGED={'true' if impact.runtime_changed else 'false'}")
    print(f"DB_SENSITIVE_CHANGED={'true' if impact.db_sensitive_changed else 'false'}")
    print(f"BASE_SHA={base_sha}")
    print(f"TARGET_SHA={target_sha}")
    print("CHANGED_PATHS_JSON=" + json.dumps(impact.changed_paths, ensure_ascii=False))
    print("CONTROL_PLANE_PATHS_JSON=" + json.dumps(impact.control_plane_paths, ensure_ascii=False))
    print("RUNTIME_PATHS_JSON=" + json.dumps(impact.runtime_paths, ensure_ascii=False))
    print("DB_SENSITIVE_PATHS_JSON=" + json.dumps(impact.db_sensitive_paths, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="base commit/ref; defaults to TARGET^")
    parser.add_argument("--target", default="HEAD", help="target commit/ref (default: HEAD)")
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args(argv)

    base = args.base or _default_base(args.target)
    try:
        base_sha, target_sha, paths = changed_paths_between(base, args.target)
        impact = classify_paths(paths)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: deploy-impact classification failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                impact.as_dict(base_sha=base_sha, target_sha=target_sha),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        _print_env(impact, base_sha=base_sha, target_sha=target_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
