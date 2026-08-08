#!/usr/bin/env python3
"""Versioned, atomic per-source health state for Hermes Tech RSS collection."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1
CONSECUTIVE_FAILURE_WARNING = 3
STALE_SUCCESS_HOURS = 48


class SourceHealthError(RuntimeError):
    """Raised when source-health state is malformed or cannot be persisted."""


def empty_state() -> dict[str, Any]:
    return {"format_version": FORMAT_VERSION, "sources": {}}


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SourceHealthError(f"invalid source-health timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SourceHealthError(f"source-health timestamp has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise SourceHealthError("source-health root must be an object")
    if state.get("format_version") != FORMAT_VERSION:
        raise SourceHealthError(
            f"unsupported source-health format_version={state.get('format_version')!r}"
        )
    sources = state.get("sources")
    if not isinstance(sources, dict):
        raise SourceHealthError("source-health sources must be an object")

    for name, entry in sources.items():
        if not isinstance(name, str) or not name.strip():
            raise SourceHealthError("source-health source name must be non-empty text")
        if not isinstance(entry, dict):
            raise SourceHealthError(f"source-health entry for {name!r} must be an object")
        failures = entry.get("consecutive_failures", 0)
        if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
            raise SourceHealthError(
                f"source-health consecutive_failures invalid for {name!r}"
            )
        for field in ("last_success_at", "last_failure_at"):
            value = entry.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise SourceHealthError(f"{field} must be text or null for {name!r}")
                _parse_timestamp(value)
    return state


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceHealthError(f"cannot read source-health state: {exc}") from exc
    return validate_state(payload)


def save_state(path: Path, state: dict[str, Any]) -> None:
    validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise SourceHealthError(f"cannot persist source-health state: {exc}") from exc
    finally:
        if tmp.exists():
            tmp.unlink()


def _entry(state: dict[str, Any], name: str) -> dict[str, Any]:
    sources = state["sources"]
    current = sources.get(name)
    if current is None:
        current = {
            "last_success_at": None,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "last_status": "unknown",
        }
        sources[name] = current
    return current


def record_success(
    state: dict[str, Any],
    name: str,
    *,
    at: str,
    http_status: int,
    final_host: str,
    content_type: str,
    bytes_received: int,
    redirects: int,
    feed_entries: int,
    new_articles: int,
) -> None:
    _parse_timestamp(at)
    entry = _entry(state, name)
    entry.update(
        {
            "last_success_at": at,
            "consecutive_failures": 0,
            "last_status": "ok",
            "last_http_status": int(http_status),
            "last_final_host": str(final_host),
            "last_content_type": str(content_type),
            "last_bytes": int(bytes_received),
            "last_redirects": int(redirects),
            "last_feed_entries": int(feed_entries),
            "last_new_articles": int(new_articles),
            "last_error_type": None,
        }
    )


def record_failure(
    state: dict[str, Any],
    name: str,
    *,
    at: str,
    error_type: str,
) -> None:
    _parse_timestamp(at)
    entry = _entry(state, name)
    entry.update(
        {
            "last_failure_at": at,
            "consecutive_failures": int(entry.get("consecutive_failures", 0)) + 1,
            "last_status": "failed",
            "last_error_type": error_type or "UnknownError",
        }
    )


def health_warnings(
    state: dict[str, Any],
    configured_sources: list[str],
    *,
    now: datetime | None = None,
) -> list[str]:
    validate_state(state)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    warnings: list[str] = []
    sources = state["sources"]

    for name in configured_sources:
        entry = sources.get(name)
        if not isinstance(entry, dict):
            warnings.append(f"{name}: no health history yet")
            continue

        failures = int(entry.get("consecutive_failures", 0))
        if failures >= CONSECUTIVE_FAILURE_WARNING:
            warnings.append(f"{name}: {failures} consecutive fetch failures")

        last_success = entry.get("last_success_at")
        if isinstance(last_success, str):
            age_hours = (current - _parse_timestamp(last_success)).total_seconds() / 3600
            if age_hours >= STALE_SUCCESS_HOURS:
                warnings.append(
                    f"{name}: last successful fetch is {age_hours:.1f}h old"
                )
        elif failures:
            warnings.append(f"{name}: no successful fetch recorded yet")

    return warnings
