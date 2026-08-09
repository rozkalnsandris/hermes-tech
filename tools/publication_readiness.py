#!/usr/bin/env python3
"""Fail-closed infrastructure readiness gate for scheduled digest publication.

The check path is intentionally secret-free and read-only. It refuses to start a
scheduled digest run unless production is the exact current main SHA, the
pull-deploy readiness record is CURRENT for that SHA, the tracked runtime
contract matches the active production interpreter/environment, and SQLite is
healthy at the current schema without an apply plan.

Exit status 79 means "publication not ready". No content generation, model call,
publication, or database mutation is authorized by this tool.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

EXIT_OPERATIONAL = 1
EXIT_USAGE = 2
EXIT_NOT_READY = 79
FETCH_TIMEOUT_SECONDS = 30
CHECK_TIMEOUT_SECONDS = 60
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

CURRENT = "CURRENT"
BLOCKED_DEPLOY_REASONS = {
    "WAIT_CI",
    "WAIT_CONTROL_PLANE_APPROVAL",
    "RUNTIME_ROLLOUT_REQUIRED",
    "DB_APPLY_REQUIRES_SEPARATE_APPROVAL",
    "DEPLOY_FAILED",
}


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    reason: str
    target_sha: str
    production_sha: str
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "target_sha": self.target_sha,
            "production_sha": self.production_sha,
            "detail": self.detail,
        }


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = CHECK_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _git(root: Path, *args: str, timeout: int = CHECK_TIMEOUT_SECONDS) -> str:
    proc = _run(["git", *args], cwd=root, timeout=timeout)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return proc.stdout.strip()


def _safe_sha(value: str) -> str:
    return value if SHA_RE.fullmatch(value) else "unknown"


def load_readiness_state(state_root: Path) -> dict[str, Any] | None:
    path = state_root / "readiness.json"
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"unsafe readiness state path: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("unsupported readiness state")
    return payload


def _state_reason_for_range(
    state: dict[str, Any] | None,
    *,
    target_sha: str,
    production_sha: str,
) -> str | None:
    if not state:
        return None
    if (
        state.get("target_sha") == target_sha
        and state.get("production_sha") == production_sha
        and state.get("reason") in BLOCKED_DEPLOY_REASONS
    ):
        return str(state["reason"])
    return None


def check_git_and_deploy_state(root: Path, state_root: Path) -> ReadinessResult:
    production_sha = "unknown"
    target_sha = "unknown"
    try:
        proc = _run(
            ["git", "fetch", "--prune", "origin", "main"],
            cwd=root,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "git fetch failed"
            return ReadinessResult(
                False,
                "GIT_FETCH_FAILED",
                target_sha,
                production_sha,
                detail,
            )

        branch = _git(root, "branch", "--show-current")
        production_sha = _git(root, "rev-parse", "HEAD")
        target_sha = _git(root, "rev-parse", "refs/remotes/origin/main")
        if SHA_RE.fullmatch(production_sha) is None or SHA_RE.fullmatch(target_sha) is None:
            return ReadinessResult(
                False,
                "INVALID_GIT_SHA",
                _safe_sha(target_sha),
                _safe_sha(production_sha),
                "git returned a non-canonical commit SHA",
            )
        if branch != "main":
            return ReadinessResult(
                False,
                "PRODUCTION_BRANCH_NOT_MAIN",
                target_sha,
                production_sha,
                f"branch={branch or 'detached'}",
            )

        try:
            state = load_readiness_state(state_root)
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            return ReadinessResult(
                False,
                "READINESS_STATE_INVALID",
                target_sha,
                production_sha,
                str(exc),
            )

        if production_sha != target_sha:
            reason = _state_reason_for_range(
                state,
                target_sha=target_sha,
                production_sha=production_sha,
            )
            return ReadinessResult(
                False,
                reason or "PRODUCTION_BEHIND_MAIN",
                target_sha,
                production_sha,
                "production HEAD does not equal current origin/main",
            )

        if state is None:
            return ReadinessResult(
                False,
                "READINESS_STATE_ABSENT",
                target_sha,
                production_sha,
                "pull-deploy readiness state is missing",
            )
        if not (
            state.get("reason") == CURRENT
            and state.get("target_sha") == target_sha
            and state.get("production_sha") == production_sha
        ):
            return ReadinessResult(
                False,
                "READINESS_STATE_NOT_CURRENT",
                target_sha,
                production_sha,
                "pull-deploy readiness state is not CURRENT for exact main",
            )

        return ReadinessResult(True, CURRENT, target_sha, production_sha)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return ReadinessResult(
            False,
            "GIT_PREFLIGHT_FAILED",
            _safe_sha(target_sha),
            _safe_sha(production_sha),
            str(exc),
        )


def check_runtime(root: Path, result: ReadinessResult) -> ReadinessResult:
    try:
        supported = (root / ".python-version").read_text(encoding="utf-8").strip()
    except OSError as exc:
        return ReadinessResult(
            False,
            "RUNTIME_CONTRACT_UNAVAILABLE",
            result.target_sha,
            result.production_sha,
            str(exc),
        )

    actual = ".".join(map(str, sys.version_info[:3]))
    if actual != supported:
        return ReadinessResult(
            False,
            "RUNTIME_VERSION_MISMATCH",
            result.target_sha,
            result.production_sha,
            f"supported={supported} active={actual}",
        )

    contract = _run(
        [sys.executable, str(root / "tools" / "check_dependency_sync.py")],
        cwd=root,
    )
    if contract.returncode != 0:
        detail = contract.stderr.strip() or contract.stdout.strip()
        return ReadinessResult(
            False,
            "RUNTIME_DEPENDENCY_CONTRACT_FAILED",
            result.target_sha,
            result.production_sha,
            detail,
        )

    pip_check = _run([sys.executable, "-m", "pip", "check"], cwd=root)
    if pip_check.returncode != 0:
        detail = pip_check.stderr.strip() or pip_check.stdout.strip()
        return ReadinessResult(
            False,
            "RUNTIME_DEPENDENCY_HEALTH_FAILED",
            result.target_sha,
            result.production_sha,
            detail,
        )

    return result


def check_database(root: Path, result: ReadinessResult) -> ReadinessResult:
    proc = _run(
        [
            sys.executable,
            str(root / "tools" / "sqlite_schema.py"),
            "preflight",
            "--db",
            str(root / "data" / "hermes.db"),
        ],
        cwd=root,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        return ReadinessResult(
            False,
            "DB_PREFLIGHT_FAILED",
            result.target_sha,
            result.production_sha,
            detail,
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return ReadinessResult(
            False,
            "DB_PREFLIGHT_INVALID",
            result.target_sha,
            result.production_sha,
            str(exc),
        )

    if payload.get("quick_check") != ["ok"]:
        return ReadinessResult(
            False,
            "DB_QUICK_CHECK_FAILED",
            result.target_sha,
            result.production_sha,
            f"quick_check={payload.get('quick_check')!r}",
        )
    if payload.get("needs_change") is not False:
        return ReadinessResult(
            False,
            "DB_SCHEMA_NOT_CURRENT",
            result.target_sha,
            result.production_sha,
            f"plan={payload.get('plan')!r}",
        )
    if payload.get("user_version") != payload.get("current_schema_version"):
        return ReadinessResult(
            False,
            "DB_SCHEMA_VERSION_MISMATCH",
            result.target_sha,
            result.production_sha,
            "SQLite user_version does not match supported schema",
        )
    return result


def evaluate(root: Path, state_root: Path) -> ReadinessResult:
    root = root.resolve()
    result = check_git_and_deploy_state(root, state_root.resolve())
    if not result.ready:
        return result
    result = check_runtime(root, result)
    if not result.ready:
        return result
    return check_database(root, result)


def build_notification(reason: str, target_sha: str, production_sha: str) -> str:
    return "\n".join(
        [
            "⛔ Hermes Tech daily publication blocked",
            f"Reason: {reason}",
            f"Main: {target_sha}",
            f"Production: {production_sha}",
            "Published: 0/3",
            "Model calls: 0",
            "Database mutation: no",
            "Reconcile production readiness before rerunning the daily pipeline.",
        ]
    )


def send_notification(root: Path, reason: str, target_sha: str, production_sha: str) -> bool:
    digest_path = root / "digest.py"
    if not digest_path.is_file():
        raise RuntimeError(f"digest.py not found: {digest_path}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("hermes_publication_readiness_notify", digest_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load digest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "load_env") or not hasattr(module, "send_telegram"):
        raise RuntimeError("digest.py notification contract unavailable")
    env = module.load_env()
    return bool(
        module.send_telegram(
            env,
            build_notification(reason, target_sha, production_sha),
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--root", required=True, type=Path)
    check.add_argument("--state-root", required=True, type=Path)
    check.add_argument("--json", action="store_true")

    notify = sub.add_parser("notify")
    notify.add_argument("--root", required=True, type=Path)
    notify.add_argument("--reason", required=True)
    notify.add_argument("--target-sha", required=True)
    notify.add_argument("--production-sha", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "notify":
            for value in (args.target_sha, args.production_sha):
                if value != "unknown" and SHA_RE.fullmatch(value) is None:
                    raise ValueError(f"invalid SHA: {value!r}")
            ok = send_notification(
                args.root.resolve(),
                args.reason,
                args.target_sha,
                args.production_sha,
            )
            print("PUBLISH_READINESS_NOTIFY=" + ("PASS" if ok else "FAILED"))
            return 0 if ok else EXIT_OPERATIONAL

        result = evaluate(args.root, args.state_root)
        if args.json:
            print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
        else:
            print("PUBLISH_READINESS=" + ("READY" if result.ready else "BLOCKED"))
            print(f"PUBLISH_READINESS_REASON={result.reason}")
            print(f"TARGET_SHA={result.target_sha}")
            print(f"PRODUCTION_SHA={result.production_sha}")
        return 0 if result.ready else EXIT_NOT_READY
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: publication readiness failed: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL


if __name__ == "__main__":
    raise SystemExit(main())
