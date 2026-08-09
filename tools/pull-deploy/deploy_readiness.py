#!/usr/bin/env python3
"""Persist pull-deploy readiness and notify on meaningful state transitions.

The helper is intentionally independent of the production virtualenv so it can
report a stale or runtime-broken production checkout. State contains no secrets.
Telegram credentials are read from the production .env only at send time and are
redacted from every diagnostic.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

SCHEMA_VERSION = 1
STATE_FILENAME = "readiness.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REASONS = {
    "CURRENT",
    "WAIT_CI",
    "WAIT_CONTROL_PLANE_APPROVAL",
    "RUNTIME_ROLLOUT_REQUIRED",
    "DB_APPLY_REQUIRES_SEPARATE_APPROVAL",
    "DEPLOY_FAILED",
}


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        return {}
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        env[key] = value
    return env


def redact(value: object, *secrets: str) -> str:
    text = str(value)
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"https://api\.telegram\.org/bot[^/\s]+/sendMessage",
        "https://api.telegram.org/bot[REDACTED]/sendMessage",
        text,
    )
    return text[:500]


def load_state(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"readiness state path is unsafe: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("unsupported readiness state")
    return data


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def transition_message(
    previous: dict[str, object] | None,
    *,
    reason: str,
    target_sha: str,
    production_sha: str,
) -> str | None:
    previous_reason = str(previous.get("reason", "")) if previous else ""
    if reason == "CURRENT":
        if previous is None or previous_reason == "CURRENT":
            return None
        return "\n".join(
            [
                "✅ Hermes Tech production is current again",
                f"Main: {target_sha}",
                f"Production: {production_sha}",
                f"Recovered from: {previous_reason}",
            ]
        )

    return "\n".join(
        [
            "⚠️ Hermes Tech production is not publish-ready",
            f"Reason: {reason}",
            f"Main: {target_sha}",
            f"Production: {production_sha}",
            "Daily publication must wait until production is reconciled.",
        ]
    )


def send_telegram(env_file: Path, text: str) -> bool:
    env = read_dotenv(env_file)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("READINESS_NOTIFY=SKIPPED_NOT_CONFIGURED")
        return False

    api = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        api,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            if not 200 <= status < 300:
                print(f"READINESS_NOTIFY=FAILED_HTTP_{status}")
                return False
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        detail = redact(exc, token, chat_id)
        print(f"READINESS_NOTIFY=FAILED {type(exc).__name__}: {detail}")
        return False

    print("READINESS_NOTIFY=PASS")
    return True


def record(
    *,
    state_root: Path,
    env_file: Path,
    reason: str,
    target_sha: str,
    production_sha: str,
    notify: bool = True,
) -> tuple[bool, dict[str, object]]:
    if reason not in REASONS:
        raise ValueError(f"unsupported readiness reason: {reason}")
    for label, sha in (("target", target_sha), ("production", production_sha)):
        if SHA_RE.fullmatch(sha) is None:
            raise ValueError(f"invalid {label} SHA: {sha!r}")

    state_path = state_root / STATE_FILENAME
    previous = load_state(state_path)
    key = (reason, target_sha, production_sha)
    previous_key = None
    if previous:
        previous_key = (
            str(previous.get("reason", "")),
            str(previous.get("target_sha", "")),
            str(previous.get("production_sha", "")),
        )

    now = utc_now()
    same_target = bool(previous and previous.get("target_sha") == target_sha)
    first_seen = (
        str(previous.get("first_seen_utc"))
        if same_target and previous and previous.get("first_seen_utc")
        else now
    )
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "reason": reason,
        "target_sha": target_sha,
        "production_sha": production_sha,
        "first_seen_utc": first_seen,
        "last_seen_utc": now,
    }
    atomic_write_json(state_path, payload)

    changed = key != previous_key
    print(f"READINESS_STATE={'CHANGED' if changed else 'UNCHANGED'}")
    print(f"READINESS_REASON={reason}")
    print(f"TARGET_SHA={target_sha}")
    print(f"PRODUCTION_SHA={production_sha}")
    print(f"READINESS_FIRST_SEEN_UTC={first_seen}")

    if not changed:
        return False, payload

    message = transition_message(
        previous,
        reason=reason,
        target_sha=target_sha,
        production_sha=production_sha,
    )
    if message is None:
        print("READINESS_NOTIFY=NO_TRANSITION_MESSAGE")
    elif notify:
        send_telegram(env_file, message)
    else:
        print("READINESS_NOTIFY=DISABLED")
    return True, payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record_parser = sub.add_parser("record")
    record_parser.add_argument("--state-root", required=True, type=Path)
    record_parser.add_argument("--env-file", required=True, type=Path)
    record_parser.add_argument("--reason", required=True, choices=sorted(REASONS))
    record_parser.add_argument("--target-sha", required=True)
    record_parser.add_argument("--production-sha", required=True)
    record_parser.add_argument("--no-notify", action="store_true")
    show_parser = sub.add_parser("show")
    show_parser.add_argument("--state-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "record":
            record(
                state_root=args.state_root,
                env_file=args.env_file,
                reason=args.reason,
                target_sha=args.target_sha,
                production_sha=args.production_sha,
                notify=not args.no_notify,
            )
            return 0
        state = load_state(args.state_root / STATE_FILENAME)
        if state is None:
            print("READINESS_STATE=ABSENT")
        else:
            print(json.dumps(state, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: deploy readiness state failed: {redact(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
