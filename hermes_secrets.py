#!/usr/bin/env python3
"""Small fail-safe redaction helpers for runtime diagnostics.

The helpers in this module are intentionally deterministic and network-free.
They are for log/error text only; they do not transform configuration values
before those values are used by their owning integrations.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

REDACTED = "<redacted>"
_TELEGRAM_BOT_URL = re.compile(
    r"(?P<prefix>https?://api\.telegram\.org/bot)"
    r"(?P<credential>[^/\s?#]+)"
    r"(?P<suffix>/[^\s?#]*)?",
    re.IGNORECASE,
)


def _normalise_secrets(values: Iterable[object]) -> list[str]:
    secrets = {
        str(value)
        for value in values
        if value is not None and str(value)
    }
    return sorted(secrets, key=len, reverse=True)


def redact_secret_text(text: object, *secrets: object) -> str:
    """Return diagnostic text with known secrets and Telegram bot URLs redacted.

    Exact configured values are replaced longest-first so overlapping values do
    not leave a useful suffix behind. Telegram Bot API credentials are also
    redacted structurally as defense in depth when the caller does not have the
    original token value available.
    """
    rendered = str(text)
    for secret in _normalise_secrets(secrets):
        rendered = rendered.replace(secret, REDACTED)

    def replace_telegram(match: re.Match[str]) -> str:
        suffix = match.group("suffix") or ""
        return f"{match.group('prefix')}{REDACTED}{suffix}"

    return _TELEGRAM_BOT_URL.sub(replace_telegram, rendered)
