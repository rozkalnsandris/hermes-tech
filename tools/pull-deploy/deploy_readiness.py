#!/usr/bin/env python3
"""Persist pull-deploy readiness, transition notices, and bounded-age escalation.

The helper is intentionally independent of the production virtualenv so it can
report a stale or runtime-broken production checkout. State contains no secrets.
Telegram credentials are read from the production .env only at send time and are
redacted from every diagnostic.

The watchdog is informational only. It never approves a control-plane change,
rebuilds a runtime, migrates a database, or performs a deployment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
STATE_FILENAME = "readiness.json"
LOCK_FILENAME = "readiness.lock"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REASONS = {
    "CURRENT",
    "WAIT_CI",
    "WAIT_CONTROL_PLANE_APPROVAL",
    "RUNTIME_ROLLOUT_REQUIRED",
    "DB_APPLY_REQUIRES_SEPARATE_APPROVAL",
    "DEPLOY_FAILED",
}
DEPLOY_IMPACTS = {
    "NO_DEPLOY",
    "AUTO_DEPLOY_SAFE",
    "CONTROL_PLANE_APPROVAL_REQUIRED",
    "RUNTIME_ROLLOUT_REQUIRED",
    "DB_APPLY_REQUIRES_SEPARATE_APPROVAL",
    "UNCLASSIFIED",
}
WATCHDOG_LEVEL_NONE = "NONE"
WATCHDOG_LEVEL_AGED = "AGED"
WATCHDOG_GRACE = timedelta(hours=2)
PUBLICATION_HOUR_LOCAL = 7
PRE_PUBLICATION_BUFFER = timedelta(hours=1)
BUSINESS_TZ = ZoneInfo("Europe/Berlin")


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC datetime must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def utc_now() -> str:
    return format_utc(utc_now_dt())


def parse_utc(value: object) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


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
    if not isinstance(data, dict):
        raise RuntimeError("unsupported readiness state")
    schema = data.get("schema_version")
    if schema not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
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


@contextmanager
def state_write_lock(state_root: Path):
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_root, 0o700)
    lock_path = state_root / LOCK_FILENAME
    if lock_path.exists() and (not lock_path.is_file() or lock_path.is_symlink()):
        raise RuntimeError(f"readiness lock path is unsafe: {lock_path}")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8", closefd=False) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        os.close(fd)


def transition_message(
    previous: dict[str, object] | None,
    *,
    reason: str,
    target_sha: str,
    production_sha: str,
    deploy_impact: str,
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
            f"Impact: {deploy_impact}",
            f"Main: {target_sha}",
            f"Production: {production_sha}",
            "Daily publication must wait until production is reconciled.",
        ]
    )


def watchdog_deadline(first_seen_utc: datetime) -> datetime:
    """Return the earlier of the 2h SLO and the pre-07:00 safety deadline."""
    first_seen_utc = first_seen_utc.astimezone(timezone.utc)
    local = first_seen_utc.astimezone(BUSINESS_TZ)

    if local.hour < PUBLICATION_HOUR_LOCAL:
        publication_date = local.date()
    else:
        publication_date = date.fromordinal(local.date().toordinal() + 1)

    safety_hour = PUBLICATION_HOUR_LOCAL - int(
        PRE_PUBLICATION_BUFFER.total_seconds() // 3600
    )
    safety_local = datetime.combine(
        publication_date,
        time(hour=safety_hour),
        tzinfo=BUSINESS_TZ,
    )
    safety_utc = safety_local.astimezone(timezone.utc)

    deadline = min(first_seen_utc + WATCHDOG_GRACE, safety_utc)
    return max(deadline, first_seen_utc)


def watchdog_escalation_message(
    *,
    reason: str,
    deploy_impact: str,
    target_sha: str,
    production_sha: str,
    first_seen_utc: datetime,
    deadline_utc: datetime,
    now_utc: datetime,
) -> str:
    age_minutes = max(
        0,
        int((now_utc - first_seen_utc).total_seconds() // 60),
    )
    return "\n".join(
        [
            "🚨 Hermes Tech deploy readiness SLO breached",
            f"Reason: {reason}",
            f"Impact: {deploy_impact}",
            f"Main: {target_sha}",
            f"Production: {production_sha}",
            f"Unreconciled age: {age_minutes} min",
            f"SLO deadline: {format_utc(deadline_utc)}",
            "Daily publication remains protected by the readiness gate.",
            "Watchdog is informational only; no approval, migration, or deploy was performed.",
        ]
    )


def diagnostic_payload(
    state: dict[str, object] | None,
    *,
    now_utc: datetime | None = None,
) -> dict[str, object]:
    if state is None:
        return {
            "state": "ABSENT",
            "watchdog_level": WATCHDOG_LEVEL_NONE,
            "escalation_due": False,
        }

    now = (now_utc or utc_now_dt()).astimezone(timezone.utc)
    first_seen = parse_utc(state.get("first_seen_utc", ""))
    reason = str(state.get("reason", ""))
    deploy_impact = str(state.get("deploy_impact", "UNCLASSIFIED"))
    target_sha = str(state.get("target_sha", state.get("main_sha", "")))
    production_sha = str(state.get("production_sha", ""))
    level = str(state.get("watchdog_level", WATCHDOG_LEVEL_NONE))
    age_seconds = max(0, int((now - first_seen).total_seconds()))

    result: dict[str, object] = {
        "state": "CURRENT" if reason == "CURRENT" else "UNRECONCILED",
        "schema_version": int(state.get("schema_version", LEGACY_SCHEMA_VERSION)),
        "main_sha": target_sha,
        "target_sha": target_sha,
        "production_sha": production_sha,
        "deploy_impact": deploy_impact,
        "reason": reason,
        "first_seen_utc": format_utc(first_seen),
        "last_seen_utc": str(state.get("last_seen_utc", "")),
        "age_seconds": age_seconds,
        "watchdog_level": level,
        "escalation_due": False,
    }
    if reason != "CURRENT" and deploy_impact != "NO_DEPLOY":
        deadline = watchdog_deadline(first_seen)
        result["deadline_utc"] = format_utc(deadline)
        result["escalation_due"] = now >= deadline
    return result


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
    deploy_impact: str = "UNCLASSIFIED",
    notify: bool = True,
    now_utc: datetime | None = None,
) -> tuple[bool, dict[str, object]]:
    if reason not in REASONS:
        raise ValueError(f"unsupported readiness reason: {reason}")
    if deploy_impact not in DEPLOY_IMPACTS:
        raise ValueError(f"unsupported deploy impact: {deploy_impact}")
    for label, sha in (("target", target_sha), ("production", production_sha)):
        if SHA_RE.fullmatch(sha) is None:
            raise ValueError(f"invalid {label} SHA: {sha!r}")
    if reason == "CURRENT" and target_sha != production_sha:
        raise ValueError("CURRENT readiness requires production SHA == target SHA")

    now = (now_utc or utc_now_dt()).astimezone(timezone.utc)
    now_text = format_utc(now)
    transition_text: str | None = None
    escalation_text: str | None = None

    with state_write_lock(state_root):
        state_path = state_root / STATE_FILENAME
        previous = load_state(state_path)
        key = (reason, target_sha, production_sha, deploy_impact)
        previous_key = None
        if previous:
            previous_key = (
                str(previous.get("reason", "")),
                str(previous.get("target_sha", previous.get("main_sha", ""))),
                str(previous.get("production_sha", "")),
                str(previous.get("deploy_impact", "UNCLASSIFIED")),
            )

        same_target = bool(
            previous
            and str(previous.get("target_sha", previous.get("main_sha", "")))
            == target_sha
        )
        if reason == "CURRENT":
            first_seen_text = now_text
            watchdog_key = ""
            watchdog_level = WATCHDOG_LEVEL_NONE
        else:
            first_seen_text = (
                str(previous.get("first_seen_utc"))
                if same_target and previous and previous.get("first_seen_utc")
                else now_text
            )
            previous_watchdog_key = (
                str(previous.get("watchdog_escalation_key", ""))
                if same_target and previous
                else ""
            )
            first_seen = parse_utc(first_seen_text)
            deadline = watchdog_deadline(first_seen)
            desired_key = f"{target_sha}:{reason}:{WATCHDOG_LEVEL_AGED}"
            if (
                deploy_impact != "NO_DEPLOY"
                and now >= deadline
                and previous_watchdog_key != desired_key
            ):
                watchdog_key = desired_key
                watchdog_level = WATCHDOG_LEVEL_AGED
                escalation_text = watchdog_escalation_message(
                    reason=reason,
                    deploy_impact=deploy_impact,
                    target_sha=target_sha,
                    production_sha=production_sha,
                    first_seen_utc=first_seen,
                    deadline_utc=deadline,
                    now_utc=now,
                )
            else:
                watchdog_key = previous_watchdog_key
                watchdog_level = (
                    WATCHDOG_LEVEL_AGED
                    if watchdog_key
                    else WATCHDOG_LEVEL_NONE
                )

        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "main_sha": target_sha,
            "target_sha": target_sha,
            "production_sha": production_sha,
            "deploy_impact": deploy_impact,
            "reason": reason,
            "first_seen_utc": first_seen_text,
            "last_seen_utc": now_text,
            "watchdog_level": watchdog_level,
            "watchdog_escalation_key": watchdog_key,
        }
        atomic_write_json(state_path, payload)

        changed = key != previous_key
        if changed and escalation_text is None:
            transition_text = transition_message(
                previous,
                reason=reason,
                target_sha=target_sha,
                production_sha=production_sha,
                deploy_impact=deploy_impact,
            )

    print(f"READINESS_STATE={'CHANGED' if changed else 'UNCHANGED'}")
    print(f"READINESS_REASON={reason}")
    print(f"DEPLOY_IMPACT={deploy_impact}")
    print(f"TARGET_SHA={target_sha}")
    print(f"PRODUCTION_SHA={production_sha}")
    print(f"READINESS_FIRST_SEEN_UTC={first_seen_text}")
    print(f"WATCHDOG_LEVEL={watchdog_level}")

    message = escalation_text or transition_text
    if message is None:
        if not changed:
            print("READINESS_NOTIFY=DEDUPLICATED")
        else:
            print("READINESS_NOTIFY=NO_TRANSITION_MESSAGE")
    elif notify:
        if escalation_text is not None:
            print("WATCHDOG_ESCALATION=DUE")
        send_telegram(env_file, message)
    else:
        print("READINESS_NOTIFY=DISABLED")

    return changed, payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    record_parser = sub.add_parser("record")
    record_parser.add_argument("--state-root", required=True, type=Path)
    record_parser.add_argument("--env-file", required=True, type=Path)
    record_parser.add_argument("--reason", required=True, choices=sorted(REASONS))
    record_parser.add_argument(
        "--deploy-impact",
        default="UNCLASSIFIED",
        choices=sorted(DEPLOY_IMPACTS),
    )
    record_parser.add_argument("--target-sha", required=True)
    record_parser.add_argument("--production-sha", required=True)
    record_parser.add_argument("--no-notify", action="store_true")

    show_parser = sub.add_parser("show")
    show_parser.add_argument("--state-root", required=True, type=Path)

    diagnose_parser = sub.add_parser("diagnose")
    diagnose_parser.add_argument("--state-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "record":
            record(
                state_root=args.state_root,
                env_file=args.env_file,
                reason=args.reason,
                deploy_impact=args.deploy_impact,
                target_sha=args.target_sha,
                production_sha=args.production_sha,
                notify=not args.no_notify,
            )
            return 0

        state = load_state(args.state_root / STATE_FILENAME)
        if args.command == "diagnose":
            print(
                json.dumps(
                    diagnostic_payload(state),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        elif state is None:
            print("READINESS_STATE=ABSENT")
        else:
            print(json.dumps(state, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: deploy readiness state failed: {redact(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
