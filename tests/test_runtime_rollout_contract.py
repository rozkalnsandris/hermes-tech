from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools" / "pull-deploy" / "release" / "hermes-tech-runtime-rollout"
LAUNCHER = ROOT / "tools" / "pull-deploy" / "runtime-rollout.sh"
INSTALLER = ROOT / "tools" / "pull-deploy" / "install-pull-deploy.sh"
CLASSIFIER = ROOT / "tools" / "classify_deploy_impact.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RuntimeRolloutContractTests(unittest.TestCase):
    def test_scripts_are_syntax_valid_and_manual_only(self) -> None:
        for script in (HELPER, LAUNCHER):
            subprocess.run(["bash", "-n", str(script)], check=True)

        helper = read(HELPER)
        launcher = read(LAUNCHER)
        self.assertIn("runtime rollout helper must run as root through sudo", helper)
        self.assertIn("runtime rollout must start from the release-control worktree", launcher)
        self.assertIn("sudo --non-interactive \"$HELPER\"", launcher)
        self.assertNotIn("ExecStart=/usr/local/sbin/hermes-tech-runtime-rollout", helper)
        self.assertNotIn("ExecStart=/usr/local/sbin/hermes-tech-runtime-rollout", launcher)

    def test_exact_sha_ci_and_classifier_gates_precede_mutation(self) -> None:
        text = read(HELPER)
        for marker in (
            "target SHA is no longer exact origin/main",
            "merge-base --is-ancestor",
            '"$CLASSIFIER" --base "$OLD_SHA" --target "$TARGET_SHA" --json',
            "RUNTIME_ROLLOUT_REQUIRED",
            "runtime rollout refuses DB-sensitive target",
            "actions/workflows/ci.yml/runs",
            'row.get("head_sha") == sha',
            'row.get("name") == "validate"',
            "EXACT_TARGET_CI=PASS",
        ):
            self.assertIn(marker, text)

        mutation = text.index("MUTATION_STARTED=1")
        self.assertLess(text.index("EXACT_TARGET_CI=PASS"), mutation)
        self.assertLess(text.index("SQLITE_PREFLIGHT=PASS"), mutation)
        self.assertLess(text.index("CANDIDATE_RUNTIME=PASS"), mutation)

    def test_runtime_dependencies_use_hash_locks_and_final_path_venv(self) -> None:
        text = read(HELPER)
        self.assertGreaterEqual(text.count("--require-hashes"), 4)
        self.assertIn('"$PYTHON_RUNTIME" -m venv "$PRIMARY/venv"', text)
        self.assertIn('mv -- "$PRIMARY/venv" "$OLD_VENV_BACKUP"', text)
        self.assertNotIn('mv -- "$CANDIDATE_VENV" "$PRIMARY/venv"', text)
        self.assertIn("make altinstall", text)
        self.assertIn("www.python.org/ftp/python", text)
        self.assertIn(
            "272179ddd9a2e41a0fc8e42e33dfbdca0b3711aa5abf372d3f2d51543d09b625",
            text,
        )

    def test_sqlite_is_preflight_and_backup_only(self) -> None:
        text = read(HELPER)
        for marker in (
            'sqlite_schema.py" preflight',
            "SQLITE_ONLINE_BACKUP=PASS",
            "verify_database_unchanged",
            "DATABASE_MIGRATIONS_EXECUTED=false",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("sqlite_schema.py apply", text)

    def test_pending_digests_and_locks_are_bounded_and_immutable(self) -> None:
        text = read(HELPER)
        for marker in (
            "capture_pending_digest_state",
            "verify_pending_digest_state",
            "pending-digests.sha256",
            "target commit already tracks pending digest path",
            "pending digest files span multiple dates",
            'flock -w "$LOCK_WAIT_SECONDS" 8',
            'flock -w "$LOCK_WAIT_SECONDS" 7',
            'flock -w "$LOCK_WAIT_SECONDS" 6',
            'flock -w "$LOCK_WAIT_SECONDS" 9',
        ):
            self.assertIn(marker, text)
        self.assertNotIn("flock 9\n", text)

    def test_hugo_modes_and_phase_safe_rollback_cover_incident_115_failures(self) -> None:
        text = read(HELPER)
        for marker in (
            'chmod 755 "$WORK/site-new"',
            'find "$WORK/site-new" -type d -exec chmod 755 {} +',
            'find "$WORK/site-new" -type f -exec chmod 644 {} +',
            "STAGING_PUBLIC_PERMISSIONS=PASS",
            "PRODUCTION_PUBLIC_PERMISSIONS=PASS",
            "OLD_VENV_MOVED=0",
            "GIT_MOVED=0",
            "PUBLIC_BACKUP_READY=0",
            "rollback_runtime",
            "RUNTIME_ROLLOUT_ROLLBACK=PASS",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("ACTUAL_SENSITIVE", text)
        self.assertNotIn("EXPECTED_SENSITIVE", text)

    def test_preflight_only_and_control_plane_handoff_are_explicit(self) -> None:
        helper = read(HELPER)
        installer = read(INSTALLER)
        classifier = read(CLASSIFIER)
        self.assertIn("--preflight-only", helper)
        self.assertIn("RUNTIME_ROLLOUT_PREFLIGHT=PASS", helper)
        self.assertIn("CONTROL_PLANE_ACTIVATION_REQUIRED=true", helper)
        self.assertIn("WAIT_CONTROL_PLANE_APPROVAL", helper)
        self.assertIn("tools/pull-deploy/", classifier)
        self.assertIn("/usr/local/sbin/hermes-tech-runtime-rollout", installer)
        self.assertIn("NOPASSWD: /usr/local/sbin/hermes-tech-runtime-rollout *", installer)


if __name__ == "__main__":
    unittest.main()
