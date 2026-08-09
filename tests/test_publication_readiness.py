from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "publication_readiness.py"
RUNNER = ROOT / "run_digests.sh"
CORE = ROOT / "run_digests_core.sh"
SPEC = importlib.util.spec_from_file_location("publication_readiness", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class PublicationReadinessFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.prod = self.root / "prod"
        self.state_root = self.root / "state"

        self._run("git", "init", "--bare", "--quiet", str(self.remote))
        self._run("git", "init", "--quiet", "--initial-branch=main", str(self.seed))
        self._git(self.seed, "config", "user.name", "Hermes Test")
        self._git(self.seed, "config", "user.email", "test@example.invalid")
        (self.seed / "base.txt").write_text("base\n", encoding="utf-8")
        self._git(self.seed, "add", "base.txt")
        self._git(self.seed, "commit", "--quiet", "-m", "base")
        self._git(self.seed, "remote", "add", "origin", str(self.remote))
        self._git(self.seed, "push", "--quiet", "-u", "origin", "main")
        self._run(
            "git",
            f"--git-dir={self.remote}",
            "symbolic-ref",
            "HEAD",
            "refs/heads/main",
        )
        self._run("git", "clone", "--quiet", str(self.remote), str(self.prod))

        (self.prod / "data").mkdir()
        (self.prod / "digests").mkdir()
        (self.prod / "data" / "hermes.db").write_bytes(b"fixture-db")
        (self.prod / "digests" / "2026-08-09-devops.md").write_bytes(b"fixture-digest")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _run(*args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(
            list(args),
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return proc.stdout.strip()

    def _git(self, root: Path, *args: str) -> str:
        return self._run("git", *args, cwd=root)

    def _advance_remote(self) -> tuple[str, str]:
        production_sha = self._git(self.prod, "rev-parse", "HEAD")
        with (self.seed / "base.txt").open("a", encoding="utf-8") as handle:
            handle.write("next\n")
        self._git(self.seed, "add", "base.txt")
        self._git(self.seed, "commit", "--quiet", "-m", "next")
        self._git(self.seed, "push", "--quiet", "origin", "main")
        target_sha = self._git(self.seed, "rev-parse", "HEAD")
        return production_sha, target_sha

    def _write_state(self, reason: str, target_sha: str, production_sha: str) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "reason": reason,
            "target_sha": target_sha,
            "production_sha": production_sha,
            "first_seen_utc": "2026-08-09T00:00:00Z",
            "last_seen_utc": "2026-08-09T00:00:00Z",
        }
        (self.state_root / "readiness.json").write_text(
            json.dumps(payload) + "\n",
            encoding="utf-8",
        )

    def test_production_behind_main_blocks_without_touching_digest_or_db(self) -> None:
        production_sha, target_sha = self._advance_remote()
        db = self.prod / "data" / "hermes.db"
        digest = self.prod / "digests" / "2026-08-09-devops.md"
        db_before = sha256(db.read_bytes()).hexdigest()
        digest_before = sha256(digest.read_bytes()).hexdigest()

        result = MODULE.check_git_and_deploy_state(self.prod, self.state_root)

        self.assertFalse(result.ready)
        self.assertEqual(result.reason, "PRODUCTION_BEHIND_MAIN")
        self.assertEqual(result.production_sha, production_sha)
        self.assertEqual(result.target_sha, target_sha)
        self.assertEqual(sha256(db.read_bytes()).hexdigest(), db_before)
        self.assertEqual(sha256(digest.read_bytes()).hexdigest(), digest_before)

    def test_exact_blocked_deploy_reason_is_propagated_before_pipeline(self) -> None:
        for reason in (
            "WAIT_CI",
            "WAIT_CONTROL_PLANE_APPROVAL",
            "RUNTIME_ROLLOUT_REQUIRED",
            "DB_APPLY_REQUIRES_SEPARATE_APPROVAL",
        ):
            with self.subTest(reason=reason):
                production_sha, target_sha = self._advance_remote()
                self._write_state(reason, target_sha, production_sha)
                result = MODULE.check_git_and_deploy_state(self.prod, self.state_root)
                self.assertFalse(result.ready)
                self.assertEqual(result.reason, reason)
                self._git(self.prod, "reset", "--hard", target_sha)

    def test_current_exact_state_continues_to_runtime_and_db_checks(self) -> None:
        production_sha = self._git(self.prod, "rev-parse", "HEAD")
        self._write_state("CURRENT", production_sha, production_sha)
        ready = MODULE.ReadinessResult(
            True,
            "CURRENT",
            production_sha,
            production_sha,
        )
        with patch.object(MODULE, "check_runtime", return_value=ready) as runtime_check:
            with patch.object(MODULE, "check_database", return_value=ready) as db_check:
                result = MODULE.evaluate(self.prod, self.state_root)
        self.assertTrue(result.ready)
        runtime_check.assert_called_once()
        db_check.assert_called_once()

    def test_runtime_version_mismatch_blocks_before_dependency_commands(self) -> None:
        (self.prod / ".python-version").write_text("0.0.0\n", encoding="utf-8")
        result = MODULE.ReadinessResult(True, "CURRENT", "a" * 40, "a" * 40)
        with patch.object(MODULE, "_run") as run:
            blocked = MODULE.check_runtime(self.prod, result)
        self.assertFalse(blocked.ready)
        self.assertEqual(blocked.reason, "RUNTIME_VERSION_MISMATCH")
        run.assert_not_called()

    def test_db_schema_change_requirement_blocks(self) -> None:
        result = MODULE.ReadinessResult(True, "CURRENT", "a" * 40, "a" * 40)
        payload = {
            "quick_check": ["ok"],
            "needs_change": True,
            "plan": ["upgrade"],
            "user_version": 2,
            "current_schema_version": 3,
        }
        proc = subprocess.CompletedProcess(
            args=["sqlite-preflight"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with patch.object(MODULE, "_run", return_value=proc):
            blocked = MODULE.check_database(self.prod, result)
        self.assertFalse(blocked.ready)
        self.assertEqual(blocked.reason, "DB_SCHEMA_NOT_CURRENT")

    def test_notification_is_single_actionable_summary(self) -> None:
        text = MODULE.build_notification(
            "RUNTIME_ROLLOUT_REQUIRED",
            "a" * 40,
            "b" * 40,
        )
        self.assertIn("daily publication blocked", text)
        self.assertIn("RUNTIME_ROLLOUT_REQUIRED", text)
        self.assertIn("Published: 0/3", text)
        self.assertIn("Model calls: 0", text)
        self.assertIn("Database mutation: no", text)


class PublicationReadinessRunnerContractTests(unittest.TestCase):
    def test_readiness_gate_precedes_full_core_and_has_distinct_exit(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        gate = 'READINESS_JSON=$("$PYTHON" "$READINESS" check'
        full_core = 'bash -c "$PATCHED" "$CORE"\nrc=$?'
        self.assertIn(gate, text)
        self.assertIn(full_core, text)
        self.assertLess(text.index(gate), text.index(full_core))
        self.assertIn("PUBLISH_NOT_READY_RC=79", text)
        self.assertIn("MODEL_CALLS_EXECUTED=false", text)
        self.assertIn("PUBLICATION_CALLS_EXECUTED=0", text)
        self.assertIn("DATABASE_MUTATION_EXECUTED=false", text)
        self.assertIn("PUBLISH_READINESS_TELEGRAM=PASS", text)

    def test_check_mode_bypasses_operational_gate(self) -> None:
        text = RUNNER.read_text(encoding="utf-8")
        check_path = 'if (( $# > 0 )); then\n    exec bash -c "$PATCHED" "$CORE" "$@"\nfi'
        gate = 'READINESS=$(resolve_runtime_file tools/publication_readiness.py)'
        self.assertIn(check_path, text)
        self.assertLess(text.index(check_path), text.index(gate))

    def test_cross_category_validation_remains_before_publication_phase(self) -> None:
        text = CORE.read_text(encoding="utf-8")
        validation = '"$PYTHON" "$BASE/digest.py" validate'
        publication = '=== PHASE 4: PUBLISH SUCCESSFUL CATEGORIES ==='
        self.assertIn(validation, text)
        self.assertIn(publication, text)
        self.assertLess(text.index(validation), text.index(publication))


if __name__ == "__main__":
    unittest.main()
