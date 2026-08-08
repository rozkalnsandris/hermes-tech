#!/usr/bin/env python3
"""Notification safety contracts installed by the supported digest entrypoint."""
from __future__ import annotations

import re
from typing import Any

from hermes_secrets import redact_secret_text

_INSTALL_SENTINEL = "_HERMES_NOTIFICATION_CONTRACTS_V1"
_MAX_DIAGNOSTIC_CHARS = 500


def _safe_diagnostic(value: object, *secrets: object) -> str:
    text = redact_secret_text(value, *secrets).strip()
    if not text:
        return "no diagnostic text"
    return text[:_MAX_DIAGNOSTIC_CHARS]


def install_notification_contracts(core: Any) -> None:
    """Replace Telegram delivery with a secret-safe logging implementation."""
    if getattr(core, _INSTALL_SENTINEL, False):
        return
    required = ("requests", "chunk_paragraphs", "MAX_TG_CHUNK", "log")
    if not all(hasattr(core, name) for name in required):
        return

    def send_telegram(env: dict, text: str) -> bool:
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = env.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            core.log("Telegram nav konfigurēts (.env) — digest tikai failā")
            return False

        ok = True
        api = f"https://api.telegram.org/bot{token}/sendMessage"
        for chunk in core.chunk_paragraphs(text, core.MAX_TG_CHUNK):
            try:
                response = core.requests.post(
                    api,
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=30,
                )
                if response.status_code == 400:
                    plain = re.sub(r"<[^>]+>", "", chunk)
                    response = core.requests.post(
                        api,
                        json={
                            "chat_id": chat_id,
                            "text": plain,
                            "disable_web_page_preview": True,
                        },
                        timeout=30,
                    )
                    core.log("Telegram: HTML noraidīts, nosūtīts plain fallback")
                if not response.ok:
                    detail = _safe_diagnostic(response.text, token, chat_id)
                    core.log(f"Telegram kļūda: {response.status_code} {detail}")
                    ok = False
            except core.requests.RequestException as exc:
                detail = _safe_diagnostic(exc, token, chat_id)
                core.log(
                    "Telegram tīkla kļūda: "
                    f"{type(exc).__name__}: {detail}"
                )
                ok = False
        return ok

    core.send_telegram = send_telegram
    setattr(core, _INSTALL_SENTINEL, True)
