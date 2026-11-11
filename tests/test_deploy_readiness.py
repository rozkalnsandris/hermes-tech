from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "pull-deploy" / "deploy_readiness.py"
SPEC = importlib.util.spec_from_file_location("deploy_readiness", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TARGET_A = "a" * 40
TARGET_B = "b" * 40
PROD_A = "1" * 40


class FakeResponse:
    status = 200

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class DeployReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_root = self.root / "state"
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            "TELEGRAM_BOT_TOKEN=fake-secret-token\n"
            "TELEGRAM_CHAT_ID=123456\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def record(self, reason: str, target: str = TARGET_A, production: str = PROD_A):
        return MODULE.record(
            state_root=self.state_root,
            env_file=self.env_file,
            reason=reason,
            target_sha=target,
            production_sha=production,
        )

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_new_blocked_state_notifies_once_and_deduplicates(self, urlopen) -> None:
        changed, state = self.record("RUNTIME_ROLLOUT_REQUIRED")
        self.assertTrue(changed)
        self.assertEqual(state["reason"], "RUNTIME_ROLLOUT_REQUIRED")
        self.assertEqual(urlopen.call_count, 1)

        changed, second = self.record("RUNTIME_ROLLOUT_REQUIRED")
        self.assertFalse(changed)
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(second["first_seen_utc"], state["first_seen_utc"])

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_reason_transition_for_same_sha_notifies_again(self, urlopen) -> None:
        self.record("WAIT_CI")
        self.record("WAIT_CONTROL_PLANE_APPROVAL")
        self.assertEqual(urlopen.call_count, 2)
        state = MODULE.load_state(self.state_root / MODULE.STATE_FILENAME)
        self.assertEqual(state["reason"], "WAIT_CONTROL_PLANE_APPROVAL")

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_recovery_notifies_only_after_previous_block(self, urlopen) -> None:
        self.record("CURRENT", target=TARGET_A, production=TARGET_A)
        self.assertEqual(urlopen.call_count, 0)

        self.record("WAIT_CI", target=TARGET_B, production=TARGET_A)
        self.assertEqual(urlopen.call_count, 1)
        self.record("CURRENT", target=TARGET_B, production=TARGET_B)
        self.assertEqual(urlopen.call_count, 2)

        request = urlopen.call_args_list[-1].args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIn("production is current again", payload["text"])
        self.assertIn("WAIT_CI", payload["text"])

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_state_file_is_secret_free_and_mode_0600(self, _urlopen) -> None:
        self.record("DB_APPLY_REQUIRES_SEPARATE_APPROVAL")
        path = self.state_root / MODULE.STATE_FILENAME
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("fake-secret-token", text)
        self.assertNotIn("123456", text)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.state_root.stat().st_mode & 0o777), "0o700")

    @patch.object(MODULE.urllib.request, "urlopen", side_effect=OSError("network down"))
    def test_notification_failure_does_not_erase_blocked_state(self, _urlopen) -> None:
        changed, _ = self.record("RUNTIME_ROLLOUT_REQUIRED")
        self.assertTrue(changed)
        state = MODULE.load_state(self.state_root / MODULE.STATE_FILENAME)
        self.assertEqual(state["reason"], "RUNTIME_ROLLOUT_REQUIRED")

    def test_missing_telegram_config_still_persists_state(self) -> None:
        self.env_file.unlink()
        changed, _ = self.record("WAIT_CI")
        self.assertTrue(changed)
        state = MODULE.load_state(self.state_root / MODULE.STATE_FILENAME)
        self.assertEqual(state["reason"], "WAIT_CI")

    def test_secret_bearing_exception_text_is_redacted(self) -> None:
        text = MODULE.redact(
            "failed https://api.telegram.org/botfake-secret-token/sendMessage chat=123456",
            "fake-secret-token",
            "123456",
        )
        self.assertNotIn("fake-secret-token", text)
        self.assertNotIn("123456", text)
        self.assertIn("[REDACTED]", text)

    def test_invalid_sha_and_reason_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.record(
                state_root=self.state_root,
                env_file=self.env_file,
                reason="UNKNOWN",
                target_sha=TARGET_A,
                production_sha=PROD_A,
                notify=False,
            )
        with self.assertRaises(ValueError):
            MODULE.record(
                state_root=self.state_root,
                env_file=self.env_file,
                reason="WAIT_CI",
                target_sha="bad",
                production_sha=PROD_A,
                notify=False,
            )


if __name__ == "__main__":
    unittest.main()
