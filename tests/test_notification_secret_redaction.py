#!/usr/bin/env python3
from __future__ import annotations

from types import SimpleNamespace
import unittest

import requests

from digest_notifications import install_notification_contracts
from hermes_secrets import REDACTED, redact_secret_text

FAKE_TOKEN = "123456789:AAFakeHermesAuditToken_DO_NOT_USE"
FAKE_CHAT_ID = "-1009876543210"


class RaisingRequests:
    RequestException = requests.RequestException

    @staticmethod
    def post(url: str, **_kwargs):
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='api.telegram.org', port=443): "
            f"Max retries exceeded with url: {url}"
        )


class ErrorResponse:
    status_code = 500
    ok = False
    text = f"upstream rejected bot={FAKE_TOKEN} chat={FAKE_CHAT_ID}"


class ErrorResponseRequests:
    RequestException = requests.RequestException

    @staticmethod
    def post(_url: str, **_kwargs):
        return ErrorResponse()


def fake_core(requests_impl):
    logs: list[str] = []
    core = SimpleNamespace(
        requests=requests_impl,
        MAX_TG_CHUNK=3900,
        chunk_paragraphs=lambda text, _limit: [text],
        log=logs.append,
    )
    return core, logs


class SecretRedactionTests(unittest.TestCase):
    def test_structural_telegram_url_redaction_without_known_token(self) -> None:
        text = (
            "failed for https://api.telegram.org/"
            f"bot{FAKE_TOKEN}/sendMessage"
        )
        redacted = redact_secret_text(text)
        self.assertNotIn(FAKE_TOKEN, redacted)
        self.assertIn(f"bot{REDACTED}/sendMessage", redacted)

    def test_exact_secrets_are_redacted_longest_first(self) -> None:
        text = f"token={FAKE_TOKEN} chat={FAKE_CHAT_ID}"
        redacted = redact_secret_text(text, FAKE_TOKEN, FAKE_CHAT_ID)
        self.assertNotIn(FAKE_TOKEN, redacted)
        self.assertNotIn(FAKE_CHAT_ID, redacted)
        self.assertEqual(redacted.count(REDACTED), 2)

    def test_transport_exception_cannot_log_bot_token(self) -> None:
        core, logs = fake_core(RaisingRequests)
        install_notification_contracts(core)

        ok = core.send_telegram(
            {
                "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
                "TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
            },
            "hello",
        )

        self.assertFalse(ok)
        joined = "\n".join(logs)
        self.assertNotIn(FAKE_TOKEN, joined)
        self.assertNotIn(FAKE_CHAT_ID, joined)
        self.assertIn(REDACTED, joined)
        self.assertIn("ConnectionError", joined)

    def test_error_response_body_is_redacted_too(self) -> None:
        core, logs = fake_core(ErrorResponseRequests)
        install_notification_contracts(core)

        ok = core.send_telegram(
            {
                "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
                "TELEGRAM_CHAT_ID": FAKE_CHAT_ID,
            },
            "hello",
        )

        self.assertFalse(ok)
        joined = "\n".join(logs)
        self.assertNotIn(FAKE_TOKEN, joined)
        self.assertNotIn(FAKE_CHAT_ID, joined)
        self.assertIn("Telegram kļūda: 500", joined)

    def test_installer_is_idempotent(self) -> None:
        core, _logs = fake_core(RaisingRequests)
        install_notification_contracts(core)
        first = core.send_telegram
        install_notification_contracts(core)
        self.assertIs(first, core.send_telegram)


if __name__ == "__main__":
    unittest.main()
