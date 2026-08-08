#!/usr/bin/env python3
"""Best-effort Telegram notice for digest failures before the normal summary.

This helper is invoked by run_digests.sh only after a full scheduled pipeline
returns non-zero. It inspects only the log bytes appended by that invocation. If
the normal Telegram summary already succeeded, it is a no-op; otherwise it sends
one compact early-failure alert while preserving the original pipeline exit code.
"""
from __future__ import annotations

from datetime import datetime
import html
import importlib.util
from pathlib import Path
import sys

SUMMARY_SENT_MARKER = "Telegram pipeline kopsavilkums nosūtīts"
EXCEPTION_PREFIXES = (
    "RuntimeError:",
    "ValueError:",
    "KeyError:",
    "JSONDecodeError:",
    "TypeError:",
)


def read_appended_log(log_path: Path, start_size: int) -> str:
    if not log_path.exists():
        return ""
    raw = log_path.read_bytes()
    if start_size < 0 or start_size > len(raw):
        start_size = 0
    return raw[start_size:].decode("utf-8", errors="replace")


def extract_failure_detail(log_text: str) -> str:
    lines = [line.strip() for line in log_text.splitlines() if line.strip()]
    exceptions = [line for line in lines if line.startswith(EXCEPTION_PREFIXES)]
    if exceptions:
        return exceptions[-1]
    errors = [line for line in lines if "KĻŪDA:" in line]
    if errors:
        return errors[-1]
    return "pipeline exited before a detailed error was logged"


def normal_summary_was_sent(log_text: str) -> bool:
    return SUMMARY_SENT_MARKER in log_text


def load_digest_module(root: Path):
    digest_path = root / "digest.py"
    if not digest_path.is_file():
        raise RuntimeError(f"digest.py nav atrasts: {digest_path}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("hermes_digest_failure_notify", digest_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("neizdevās ielādēt digest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        print(
            "usage: notify_pipeline_failure.py <root> <log-file> <start-size> <rc>",
            file=sys.stderr,
        )
        return 2

    root = Path(args[0]).resolve()
    log_path = Path(args[1]).resolve()
    try:
        start_size = int(args[2])
        rc = int(args[3])
    except ValueError:
        print("start-size un rc jābūt integer", file=sys.stderr)
        return 2

    segment = read_appended_log(log_path, start_size)
    if normal_summary_was_sent(segment):
        return 0

    try:
        digest = load_digest_module(root)
        env = digest.load_env()
        detail = extract_failure_detail(segment)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        text = "\n".join(
            [
                "🚨 Hermes Tech pipeline failed early",
                html.escape(timestamp),
                f"Exit code: {rc}",
                "Normal Telegram summary was not reached.",
                f"Error: {html.escape(detail)}",
            ]
        )
        ok = digest.send_telegram(env, text)
    except Exception as exc:  # best-effort notifier must never mask pipeline rc
        print(f"Telegram early-failure notifier error: {exc}", file=sys.stderr)
        return 1

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
