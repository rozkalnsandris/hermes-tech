from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "pull-deploy" / "deploy_readiness.py"
SPEC = importlib.util.spec_from_file_location("deploy_readiness_watchdog", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TARGET_A = "a" * 40
PROD_A = "1" * 40


class FakeResponse:
    status = 200

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class DeployReadinessWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_root = self.root / "state"
        self.env_file = self.root / ".env"
        self.env_file.write_text(
            "TELEGRAM_BOT_TOKEN=fake-secret-token\n"
            "TELEGRAM_CHAT_ID=123456\n",
            encoding="utf-8",
        )
        self.t0 = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)

    def record(
        self,
        reason: str,
        *,
        now: datetime,
        target: str = TARGET_A,
        production: str = PROD_A,
        impact: str = "RUNTIME_ROLLOUT_REQUIRED",
        notify: bool = True,
    ):
        return MODULE.record(
            state_root=self.state_root,
            env_file=self.env_file,
            reason=reason,
            target_sha=target,
            production_sha=production,
            deploy_impact=impact,
            notify=notify,
            now_utc=now,
        )

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_stale_target_escalates_at_deterministic_two_hour_slo(self, urlopen) -> None:
        self.record("RUNTIME_ROLLOUT_REQUIRED", now=self.t0)
        self.assertEqual(urlopen.call_count, 1)

        self.record(
            "RUNTIME_ROLLOUT_REQUIRED",
            now=self.t0 + timedelta(hours=1, minutes=59),
        )
        self.assertEqual(urlopen.call_count, 1)

        self.record(
            "RUNTIME_ROLLOUT_REQUIRED",
            now=self.t0 + timedelta(hours=2),
        )
        self.assertEqual(urlopen.call_count, 2)

        state = MODULE.load_state(self.state_root / MODULE.STATE_FILENAME)
        assert state is not None
        self.assertEqual(state["watchdog_level"], MODULE.WATCHDOG_LEVEL_AGED)
        self.assertEqual(
            state["watchdog_escalation_key"],
            f"{TARGET_A}:RUNTIME_ROLLOUT_REQUIRED:AGED",
        )

        diag = MODULE.diagnostic_payload(
            state,
            now_utc=self.t0 + timedelta(hours=2),
        )
        self.assertTrue(diag["escalation_due"])
        self.assertEqual(diag["age_seconds"], 7200)
        self.assertEqual(diag["deadline_utc"], "2026-08-09T02:00:00Z")

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_repeated_timer_runs_are_quiet_after_one_escalation(self, urlopen) -> None:
        self.record("WAIT_CONTROL_PLANE_APPROVAL", now=self.t0)
        self.record(
            "WAIT_CONTROL_PLANE_APPROVAL",
            now=self.t0 + timedelta(hours=2),
        )
        self.record(
            "WAIT_CONTROL_PLANE_APPROVAL",
            now=self.t0 + timedelta(hours=4),
        )
        self.assertEqual(urlopen.call_count, 2)

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_current_target_clears_age_clock_and_escalation(self, _urlopen) -> None:
        self.record("WAIT_CI", now=self.t0, impact="AUTO_DEPLOY_SAFE")
        self.record(
            "WAIT_CI",
            now=self.t0 + timedelta(hours=2),
            impact="AUTO_DEPLOY_SAFE",
        )
        recovered_at = self.t0 + timedelta(hours=2, minutes=5)
        self.record(
            "CURRENT",
            now=recovered_at,
            target=TARGET_A,
            production=TARGET_A,
            impact="NO_DEPLOY",
        )

        state = MODULE.load_state(self.state_root / MODULE.STATE_FILENAME)
        assert state is not None
        self.assertEqual(state["reason"], "CURRENT")
        self.assertEqual(state["first_seen_utc"], MODULE.format_utc(recovered_at))
        self.assertEqual(state["watchdog_level"], MODULE.WATCHDOG_LEVEL_NONE)
        self.assertEqual(state["watchdog_escalation_key"], "")
        diag = MODULE.diagnostic_payload(state, now_utc=recovered_at)
        self.assertFalse(diag["escalation_due"])
        self.assertEqual(diag["age_seconds"], 0)

    @patch.object(MODULE.urllib.request, "urlopen", return_value=FakeResponse())
    def test_generated_content_class_never_claims_manual_runtime_rollout(self, urlopen) -> None:
        self.record("WAIT_CI", now=self.t0, impact="AUTO_DEPLOY_SAFE")
        self.record(
            "WAIT_CI",
            now=self.t0 + timedelta(hours=2),
            impact="AUTO_DEPLOY_SAFE",
        )
        request = urlopen.call_args_list[-1].args[0]
        payload = json.loads(request.data.decode("utf-8"))
        text = payload["text"]
        self.assertIn("Impact: AUTO_DEPLOY_SAFE", text)
        self.assertNotIn("manual runtime", text.lower())
        self.assertNotIn("runtime rollout required", text.lower())
        self.assertIn("informational only", text.lower())

    def test_prepublication_deadline_is_never_after_next_0600_berlin(self) -> None:
        first_seen = datetime(2026, 8, 9, 3, 30, tzinfo=timezone.utc)
        self.assertEqual(
            MODULE.watchdog_deadline(first_seen),
            datetime(2026, 8, 9, 4, 0, tzinfo=timezone.utc),
        )

        already_late = datetime(2026, 8, 9, 4, 30, tzinfo=timezone.utc)
        self.assertEqual(MODULE.watchdog_deadline(already_late), already_late)

        after_publication = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(
            MODULE.watchdog_deadline(after_publication),
            datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc),
        )

    def test_legacy_v1_state_is_upgraded_without_losing_target_age(self) -> None:
        self.state_root.mkdir(parents=True)
        legacy = {
            "schema_version": 1,
            "reason": "WAIT_CI",
            "target_sha": TARGET_A,
            "production_sha": PROD_A,
            "first_seen_utc": MODULE.format_utc(self.t0),
            "last_seen_utc": MODULE.format_utc(self.t0),
        }
        path = self.state_root / MODULE.STATE_FILENAME
        path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

        self.record(
            "WAIT_CI",
            now=self.t0 + timedelta(minutes=30),
            impact="AUTO_DEPLOY_SAFE",
            notify=False,
        )
        upgraded = MODULE.load_state(path)
        assert upgraded is not None
        self.assertEqual(upgraded["schema_version"], 2)
        self.assertEqual(upgraded["deploy_impact"], "AUTO_DEPLOY_SAFE")
        self.assertEqual(upgraded["main_sha"], TARGET_A)
        self.assertEqual(upgraded["first_seen_utc"], MODULE.format_utc(self.t0))

    def test_concurrent_record_calls_leave_valid_secret_free_state(self) -> None:
        def invoke(index: int) -> None:
            self.record(
                "WAIT_CI",
                now=self.t0 + timedelta(seconds=index),
                impact="AUTO_DEPLOY_SAFE",
                notify=False,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(invoke, range(24)))

        state_path = self.state_root / MODULE.STATE_FILENAME
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["target_sha"], TARGET_A)
        self.assertNotIn("fake-secret-token", state_path.read_text(encoding="utf-8"))
        self.assertEqual(oct(state_path.stat().st_mode & 0o777), "0o600")
        lock_path = self.state_root / MODULE.LOCK_FILENAME
        self.assertEqual(oct(lock_path.stat().st_mode & 0o777), "0o600")

    def test_diagnostic_is_read_only_for_persisted_state(self) -> None:
        self.record(
            "WAIT_CI",
            now=self.t0,
            impact="AUTO_DEPLOY_SAFE",
            notify=False,
        )
        path = self.state_root / MODULE.STATE_FILENAME
        before = path.read_bytes()
        state = MODULE.load_state(path)
        diagnostic = MODULE.diagnostic_payload(
            state,
            now_utc=self.t0 + timedelta(minutes=10),
        )
        after = path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(diagnostic["main_sha"], TARGET_A)
        self.assertEqual(diagnostic["deploy_impact"], "AUTO_DEPLOY_SAFE")
        self.assertEqual(diagnostic["reason"], "WAIT_CI")


if __name__ == "__main__":
    unittest.main()
