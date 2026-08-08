#!/usr/bin/env python3
"""Fail-closed semantic retry for digest-shaped DeepSeek responses.

DeepSeek JSON mode can return syntactically valid JSON that still violates the
Hermes digest contract (for example, four source links for five selected items).
This wrapper retries only that model-owned response-shape failure. Transport,
HTTP, database and other runtime failures remain governed by the underlying
call and are never converted into semantic retries here.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

SEMANTIC_MAX_ATTEMPTS = 3
SEMANTIC_BACKOFF_SECONDS = (0, 1, 3)


def _digest_shape_issue(core: Any, payload: dict[str, Any]) -> str | None:
    if "selected_ids" not in payload or "digest" not in payload:
        return None

    expected = int(getattr(core, "DIGEST_ITEM_COUNT", 5))
    selected = payload.get("selected_ids")
    digest = payload.get("digest")

    if not isinstance(selected, list):
        # The underlying JSON structure validator owns this case.
        return None
    if len(selected) != expected:
        return f"selected_ids count={len(selected)}, expected={expected}"
    if not isinstance(digest, str):
        # The underlying JSON structure validator owns this case.
        return None

    extractor = getattr(core, "_extract_digest_source_candidates", None)
    if not callable(extractor):
        return None
    sources = extractor(digest)
    if len(sources) != expected:
        return f"source link count={len(sources)}, expected={expected}"

    return None


def install_digest_response_resilience(core: Any) -> None:
    """Install a bounded semantic retry around ``core.call_deepseek``."""
    if getattr(core, "_hermes_digest_response_resilience_v1", False):
        return
    if not hasattr(core, "call_deepseek"):
        return

    original: Callable[[str, str, str], str] = core.call_deepseek

    def guarded_call(api_key: str, system: str, user: str) -> str:
        last_issue: str | None = None
        for attempt in range(1, SEMANTIC_MAX_ATTEMPTS + 1):
            retry_note = ""
            if attempt > 1:
                delay = SEMANTIC_BACKOFF_SECONDS[attempt - 1]
                core.log(
                    "DeepSeek digest semantic retry "
                    f"{attempt}/{SEMANTIC_MAX_ATTEMPTS} pēc {delay}s; "
                    f"iepriekšējā atbilde: {last_issue}"
                )
                if delay:
                    time.sleep(delay)
                expected = int(getattr(core, "DIGEST_ITEM_COUNT", 5))
                retry_note = (
                    "\n\nDIGEST SEMANTIC RETRY REQUIREMENT: The previous JSON was "
                    f"syntactically valid but violated the digest shape ({last_issue}). "
                    f"Return exactly {expected} selected_ids and exactly {expected} "
                    "article sections, each with exactly one plain markdown HTTP(S) "
                    "source link. Keep all IDs restricted to the supplied candidates. "
                    "Return JSON only."
                )

            raw = original(api_key, system, user + retry_note)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                # ``original`` already owns malformed-JSON handling. If a fake or
                # future implementation violates that contract, fail closed rather
                # than layering a second parser-repair policy here.
                return raw
            if not isinstance(payload, dict):
                return raw

            issue = _digest_shape_issue(core, payload)
            if issue is None:
                if attempt > 1:
                    core.log(
                        "DeepSeek digest semantic retry izdevās "
                        f"({attempt}/{SEMANTIC_MAX_ATTEMPTS})"
                    )
                return raw

            last_issue = issue
            core.log(
                "DeepSeek digest semantic mismatch: "
                f"{issue}; atbilde noraidīta"
            )
            if attempt >= SEMANTIC_MAX_ATTEMPTS:
                raise RuntimeError(
                    "DeepSeek digest semantic retry izsmelts: " + issue
                )

        raise RuntimeError(
            "DeepSeek digest semantic retry izsmelts: "
            + (last_issue or "unknown")
        )

    core.call_deepseek = guarded_call
    core._hermes_digest_response_resilience_v1 = True
