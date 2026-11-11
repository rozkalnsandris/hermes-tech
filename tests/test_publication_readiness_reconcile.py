from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "publication_readiness_reconcile.py"
SPEC = importlib.util.spec_from_file_location("published_readiness_reconcile", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
WRITER = MODULE.state_writer

TARGET_A = "a" * 40
TARGET_B = "b" * 40


class ReadinessAutoReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_root = self.root / "state"
        self.env_file = self.root / ".env"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _approved_current(self, sha: str = TARGET_A) -> None:
        WRITER.record(
            state_root=self.state_root,
            env_file=self.env_file,
            reason="CURRENT",
            target_sha=sha,
            production_sha=sha,
            deploy_impact="NO_DEPLOY",
            notify=False,
        )

    def test_generated_content_cas_advances_only_exact_current_sha(self) -> None:
        self._approved_current()
        MODULE.verify_publication_base(
            state_root=self.state_root, expected_base_sha=TARGET_A,
        )
        changed, state = MODULE.advance_published(
            state_root=self.state_root,
            expected_base_sha=TARGET_A,
            published_sha=TARGET_B,
        )
        self.assertTrue(changed)
        self.assertEqual(state["reason"], "CURRENT")
        self.assertEqual(state["deploy_impact"], "NO_DEPLOY")
        self.assertEqual(
            [state[name] for name in ("main_sha", "target_sha", "production_sha")],
            [TARGET_B] * 3,
        )
        self.assertEqual(state["watchdog_level"], "NONE")
        self.assertEqual(state["watchdog_escalation_key"], "")
        self.assertEqual(
            oct((self.state_root / "readiness.json").stat().st_mode & 0o777),
            "0o600",
        )
        MODULE.verify_publication_base(
            state_root=self.state_root, expected_base_sha=TARGET_B,
        )

        # Three consecutive digest commits must advance from the immediate parent.
        next_sha = "c" * 40
        changed, state = MODULE.advance_published(
            state_root=self.state_root,
            expected_base_sha=TARGET_B,
            published_sha=next_sha,
        )
        self.assertTrue(changed)
        self.assertEqual(state["target_sha"], next_sha)

    def test_generated_content_cas_is_idempotent_after_exact_prior_success(self) -> None:
        self._approved_current()
        MODULE.advance_published(
            state_root=self.state_root,
            expected_base_sha=TARGET_A,
            published_sha=TARGET_B,
        )
        state_path = self.state_root / "readiness.json"
        before = state_path.read_bytes()
        changed, state = MODULE.advance_published(
            state_root=self.state_root,
            expected_base_sha=TARGET_A,
            published_sha=TARGET_B,
        )
        self.assertFalse(changed)
        self.assertEqual(state["target_sha"], TARGET_B)
        self.assertEqual(state_path.read_bytes(), before)

    def test_generated_content_cas_blocks_stale_wrong_impact_and_blocked_state(self) -> None:
        self._approved_current()
        state_path = self.state_root / "readiness.json"
        before = state_path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "publication base"):
            MODULE.advance_published(
                state_root=self.state_root,
                expected_base_sha=TARGET_B,
                published_sha="c" * 40,
            )
        self.assertEqual(state_path.read_bytes(), before)

        for reason, impact in (
            ("WAIT_CI", "AUTO_DEPLOY_SAFE"),
            ("WAIT_CONTROL_PLANE_APPROVAL", "CONTROL_PLANE_APPROVAL_REQUIRED"),
            ("DEPLOY_FAILED", "UNCLASSIFIED"),
            ("CURRENT", "AUTO_DEPLOY_SAFE"),
        ):
            with self.subTest(reason=reason, impact=impact):
                WRITER.record(
                    state_root=self.state_root,
                    env_file=self.env_file,
                    reason=reason,
                    target_sha=TARGET_A,
                    production_sha=TARGET_A,
                    deploy_impact=impact,
                    notify=False,
                )
                previous = state_path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "publication base"):
                    MODULE.verify_publication_base(
                        state_root=self.state_root,
                        expected_base_sha=TARGET_A,
                    )
                with self.assertRaisesRegex(RuntimeError, "publication base"):
                    MODULE.advance_published(
                        state_root=self.state_root,
                        expected_base_sha=TARGET_A,
                        published_sha=TARGET_B,
                    )
                self.assertEqual(state_path.read_bytes(), previous)

    def test_generated_content_cas_rejects_missing_or_unsafe_state_and_sha(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "publication base"):
            MODULE.verify_publication_base(
                state_root=self.state_root,
                expected_base_sha=TARGET_A,
            )
        self._approved_current()
        with self.assertRaisesRegex(ValueError, "invalid published SHA"):
            MODULE.advance_published(
                state_root=self.state_root,
                expected_base_sha=TARGET_A,
                published_sha="not-a-sha",
            )
        with self.assertRaisesRegex(ValueError, "must advance"):
            MODULE.advance_published(
                state_root=self.state_root,
                expected_base_sha=TARGET_A,
                published_sha=TARGET_A,
            )
        unsafe_root = self.root / "linked-state"
        unsafe_root.symlink_to(self.state_root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            MODULE.advance_published(
                state_root=unsafe_root,
                expected_base_sha=TARGET_A,
                published_sha=TARGET_B,
            )


if __name__ == "__main__":
    unittest.main()
