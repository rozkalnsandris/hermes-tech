#!/usr/bin/env python3
"""Fail-closed, compare-and-swap readiness advancement for published content.

Called only by sync_generated_content.sh after its allowlisted publication
commit has been pushed and exact local/remote SHAs have been verified. This
consumer does not mutate installed pull-deploy control-plane code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Reuse the canonical state schema, lock, and atomic-write implementation.
sys.path.insert(0, str(Path(__file__).resolve().parent / "pull-deploy"))
import deploy_readiness as state_writer  # noqa: E402


def _publication_base_state(
    state_root: Path, expected_base_sha: str,
) -> dict[str, object]:
    if state_writer.SHA_RE.fullmatch(expected_base_sha) is None:
        raise ValueError("invalid expected publication base SHA")
    if state_root.is_symlink():
        raise RuntimeError("readiness state root is unsafe")
    state = state_writer.load_state(state_root / state_writer.STATE_FILENAME)
    if (
        state is None
        or state.get("schema_version") != state_writer.SCHEMA_VERSION
        or state.get("reason") != "CURRENT"
        or state.get("deploy_impact") != "NO_DEPLOY"
        or any(
            state.get(key) != expected_base_sha
            for key in ("main_sha", "target_sha", "production_sha")
        )
    ):
        raise RuntimeError("publication base is not exact CURRENT NO_DEPLOY")
    return state


def verify_publication_base(*, state_root: Path, expected_base_sha: str) -> None:
    """Reject stale, missing, blocked, or non-content-safe readiness pre-push."""
    _publication_base_state(state_root, expected_base_sha)


def advance_published(
    *, state_root: Path, expected_base_sha: str, published_sha: str,
) -> tuple[bool, dict[str, object]]:
    """Advance CURRENT only from the exact parent state under the shared lock.

    The caller has already validated one allowlisted publication commit,
    its direct parent, and equality of local HEAD and remote main. This
    cannot convert WAIT_CI, DEPLOY_FAILED, or a newer state into CURRENT.
    """
    if state_writer.SHA_RE.fullmatch(published_sha) is None:
        raise ValueError("invalid published SHA")
    if published_sha == expected_base_sha:
        raise ValueError("publication SHA must advance")
    if state_root.is_symlink():
        raise RuntimeError("readiness state root is unsafe")

    with state_writer.state_write_lock(state_root):
        current = state_writer.load_state(
            state_root / state_writer.STATE_FILENAME
        )
        if (
            current is not None
            and current.get("schema_version") == state_writer.SCHEMA_VERSION
            and current.get("reason") == "CURRENT"
            and current.get("deploy_impact") == "NO_DEPLOY"
            and all(
                current.get(key) == published_sha
                for key in ("main_sha", "target_sha", "production_sha")
            )
        ):
            return False, current

        previous = _publication_base_state(state_root, expected_base_sha)
        now = state_writer.utc_now()
        payload = dict(previous)
        payload.update(
            main_sha=published_sha,
            target_sha=published_sha,
            production_sha=published_sha,
            first_seen_utc=now,
            last_seen_utc=now,
            watchdog_level=state_writer.WATCHDOG_LEVEL_NONE,
            watchdog_escalation_key="",
        )
        state_writer.atomic_write_json(
            state_root / state_writer.STATE_FILENAME, payload
        )
        return True, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-publish-base")
    verify.add_argument("--state-root", required=True, type=Path)
    verify.add_argument("--base-sha", required=True)
    advance = sub.add_parser("advance-published")
    advance.add_argument("--state-root", required=True, type=Path)
    advance.add_argument("--base-sha", required=True)
    advance.add_argument("--published-sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-publish-base":
            verify_publication_base(
                state_root=args.state_root,
                expected_base_sha=args.base_sha,
            )
            print("READINESS_PUBLISH_BASE=PASS")
        else:
            changed, payload = advance_published(
                state_root=args.state_root,
                expected_base_sha=args.base_sha,
                published_sha=args.published_sha,
            )
            print(
                "READINESS_PUBLISHED_ADVANCE="
                + ("CHANGED" if changed else "UNCHANGED")
            )
            print(f"TARGET_SHA={payload['target_sha']}")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"ERROR: published readiness reconciliation: "
            f"{state_writer.redact(exc)}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
