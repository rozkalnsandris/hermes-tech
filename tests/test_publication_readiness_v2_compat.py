from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "publication_readiness.py"
SPEC = importlib.util.spec_from_file_location(
    "publication_readiness_v2_compat",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TARGET = "a" * 40
PRODUCTION = "b" * 40


class PublicationReadinessV2CompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_state(self, payload: dict[str, object]) -> None:
        (self.state_root / "readiness.json").write_text(
            json.dumps(payload) + "\n",
            encoding="utf-8",
        )

    def test_current_schema_v2_is_accepted_without_mutation(self) -> None:
        payload = {
            "schema_version": 2,
            "main_sha": TARGET,
            "target_sha": TARGET,
            "production_sha": TARGET,
            "deploy_impact": "NO_DEPLOY",
            "reason": "CURRENT",
            "first_seen_utc": "2026-08-09T13:10:17Z",
            "last_seen_utc": "2026-08-09T13:10:17Z",
            "watchdog_level": "NONE",
            "watchdog_escalation_key": "",
        }
        self.write_state(payload)
        before = (self.state_root / "readiness.json").read_bytes()

        loaded = MODULE.load_readiness_state(self.state_root)

        self.assertEqual(loaded, payload)
        self.assertEqual((self.state_root / "readiness.json").read_bytes(), before)

    def test_blocked_schema_v2_reason_is_preserved(self) -> None:
        payload = {
            "schema_version": 2,
            "main_sha": TARGET,
            "target_sha": TARGET,
            "production_sha": PRODUCTION,
            "deploy_impact": "RUNTIME_ROLLOUT_REQUIRED",
            "reason": "RUNTIME_ROLLOUT_REQUIRED",
            "first_seen_utc": "2026-08-09T13:10:17Z",
            "last_seen_utc": "2026-08-09T13:10:17Z",
            "watchdog_level": "NONE",
            "watchdog_escalation_key": "",
        }
        self.write_state(payload)

        loaded = MODULE.load_readiness_state(self.state_root)
        reason = MODULE._state_reason_for_range(
            loaded,
            target_sha=TARGET,
            production_sha=PRODUCTION,
        )

        self.assertEqual(reason, "RUNTIME_ROLLOUT_REQUIRED")

    def test_schema_v2_main_target_mismatch_fails_closed(self) -> None:
        payload = {
            "schema_version": 2,
            "main_sha": "c" * 40,
            "target_sha": TARGET,
            "production_sha": TARGET,
            "deploy_impact": "NO_DEPLOY",
            "reason": "CURRENT",
            "first_seen_utc": "2026-08-09T13:10:17Z",
            "last_seen_utc": "2026-08-09T13:10:17Z",
            "watchdog_level": "NONE",
            "watchdog_escalation_key": "",
        }
        self.write_state(payload)

        with self.assertRaisesRegex(RuntimeError, "main_sha/target_sha mismatch"):
            MODULE.load_readiness_state(self.state_root)

    def test_unknown_schema_still_fails_closed(self) -> None:
        payload = {
            "schema_version": 99,
            "target_sha": TARGET,
            "production_sha": TARGET,
            "reason": "CURRENT",
        }
        self.write_state(payload)

        with self.assertRaisesRegex(RuntimeError, "unsupported readiness state"):
            MODULE.load_readiness_state(self.state_root)


if __name__ == "__main__":
    unittest.main()
