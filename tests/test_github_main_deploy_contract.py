from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-main.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
HELPER = ROOT / "tools" / "runner" / "release" / "hermes-tech-deploy-main"
RECOVERY = ROOT / "tools" / "runner" / "recover-pending-digest-deadlock.sh"
RUNNER_INSTALLER = ROOT / "tools" / "runner" / "install-github-tech-runner.sh"
HELPER_INSTALLER = ROOT / "tools" / "runner" / "install-github-main-deploy.sh"
ACTIVATOR = ROOT / "tools" / "runner" / "activate-github-main-deploy.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class GitHubMainDeployContractTests(unittest.TestCase):
    def test_workflow_queues_every_successful_main_ci_serially(self) -> None:
        text = read(WORKFLOW)

        for marker in (
            "workflow_run:",
            "workflows:\n      - CI",
            "types:\n      - completed",
            "branches:\n      - main",
            "workflow_dispatch:",
            "github.event.workflow_run.conclusion == 'success'",
            "hermes-tech-release",
            "/usr/local/sbin/hermes-tech-deploy-main",
            "https://tech.rozkalns.net/",
            "actions/upload-artifact@v6",
        ):
            self.assertIn(marker, text)

        self.assertNotIn("actions/upload-artifact@v4", text)
        self.assertNotIn("actions/checkout", text)
        self.assertNotIn("concurrency:", text)

    def test_manual_deploy_can_recover_from_exact_sha_manual_ci(self) -> None:
        text = read(WORKFLOW)
        self.assertIn(
            'row.get("event") in {"push", "workflow_dispatch"}',
            text,
        )
        self.assertIn(
            'raise SystemExit("current main has no successful exact-SHA CI run")',
            text,
        )
        # Automatic workflow_run deployment remains push-only. Manual CI is an
        # explicit recovery credential, never an automatic production trigger.
        self.assertIn('if run.get("event") != "push"', text)

    def test_main_push_ci_runs_are_not_cancelled_by_newer_merges(self) -> None:
        text = read(CI)
        self.assertIn(
            "ci-${{ github.event_name }}-${{ github.event.pull_request.number || github.sha }}",
            text,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            text,
        )
        self.assertNotIn("group: ci-${{ github.workflow }}-${{ github.ref }}", text)

    def test_root_helper_serializes_with_publisher_and_rolls_back(self) -> None:
        text = read(HELPER)
        subprocess.run(["bash", "-n", str(HELPER)], check=True)

        for marker in (
            'PUBLISH_LOCK="$PRIMARY/.publish.lock"',
            "flock 9",
            "DEPLOY_RESULT=NO_OP_ALREADY_CURRENT",
            "DEPLOY_RESULT=NO_OP_STALE",
            "hugo --source",
            'owner_git "$PRIMARY" merge --ff-only "$TARGET_SHA"',
            'owner_git "$PRIMARY" reset --hard "$OLD_SHA"',
            "DEPLOY_RESULT=FAIL_ROLLBACK_PASS",
            "DEPLOY_RESULT=PASS",
            "DATABASE_MIGRATIONS_EXECUTED=false",
            "DEPENDENCIES_CHANGED=false",
            "ROLLBACK_PERFORMED=false",
        ):
            self.assertIn(marker, text)

        self.assertNotIn("git push", text)
        self.assertNotIn("pip install", text)
        self.assertNotIn("sqlite_schema.py apply", text)
        self.assertNotIn("git checkout -B", text)
        self.assertNotIn("flock -n", text)

    def test_root_helper_preserves_only_pending_generated_digests(self) -> None:
        text = read(HELPER)

        for marker in (
            "pending_digest_date_for_path",
            "capture_pending_digest_state",
            "verify_pending_digest_state",
            "pending-digests.sha256",
            "production checkout has tracked or staged change",
            "production checkout has unrelated untracked path",
            "pending digest files span multiple dates",
            "target commit already tracks pending digest path",
            "pending digest bytes changed during deploy",
            "PENDING_GENERATED_DIGESTS_PRESERVED=",
            "sha256sum",
        ):
            self.assertIn(marker, text)

        self.assertIn(
            r"^digests/([0-9]{4}-[0-9]{2}-[0-9]{2})-(devops|ai|agents)\.md$",
            text,
        )
        self.assertIn("status=${line:0:2}", text)
        self.assertIn("[[ \"$status\" == '??' ]]", text)

    def test_owner_recovery_is_remote_ahead_only_and_republishes_existing_digests(self) -> None:
        text = read(RECOVERY)
        subprocess.run(["bash", "-n", str(RECOVERY)], check=True)

        for marker in (
            'exec 8>"$PRIMARY/.digest-pipeline.lock"',
            "flock -n 8",
            "production is not behind origin/main; this recovery only handles REMOTE_AHEAD",
            'git -C "$PRIMARY" merge-base --is-ancestor "$LOCAL_SHA" "$REMOTE_SHA"',
            "unexpected tracked/staged production change",
            "unexpected untracked production path",
            "origin/main already tracks pending digest path",
            "backup checksum mismatch",
            "restored checksum mismatch",
            'sudo --non-interactive "$DEPLOY_HELPER" "$REMOTE_SHA" "$EVIDENCE_DIR"',
            'sudo bash "$INSTALLER_REL"',
            '"$PRIMARY/digest.py" validate',
            '"$PRIMARY/digest.py" publish "$category" "$PENDING_DATE"',
            "DEPLOY_DEADLOCK_RECOVERY_RESULT=PASS",
            "DATABASE_MIGRATIONS_EXECUTED=false",
            "DEPENDENCY_INSTALL_EXECUTED=false",
            "FORCE_PUSH_EXECUTED=false",
        ):
            self.assertIn(marker, text)

        for forbidden in (
            "git push --force",
            "git reset --hard",
            "git rebase",
            "pip install",
            "sqlite_schema.py apply",
        ):
            self.assertNotIn(forbidden, text)

    def test_installers_are_narrow_and_runner_has_no_docker_group(self) -> None:
        for script in (RUNNER_INSTALLER, HELPER_INSTALLER, ACTIVATOR):
            subprocess.run(["bash", "-n", str(script)], check=True)

        runner = read(RUNNER_INSTALLER)
        helper_installer = read(HELPER_INSTALLER)
        activator = read(ACTIVATOR)

        self.assertIn("rpi5-hermes-tech-release", runner)
        self.assertIn('--labels "$RUNNER_LABEL"', runner)
        self.assertIn("RUNNER_HAS_DOCKER_GROUP=false", runner)
        self.assertIn(
            'mktemp -d "$RUNNER_HOME/.actions-runner-stage.XXXXXXXX"',
            runner,
        )
        self.assertIn('rm -rf -- "$RUNNER_DIR"', runner)
        self.assertIn('mv -- "$TMP_COPY" "$RUNNER_DIR"', runner)
        self.assertNotIn("/tmp/hermes-tech-runner-copy", runner)
        self.assertNotIn('find "$TMP_COPY" -mindepth 1', runner)

        for forbidden_state in (
            ".credentials",
            ".credentials_migrated",
            ".credentials_rsaparams",
            ".runner",
            ".runner_migrated",
            ".service",
            ".env",
            "_diag",
            "_work",
        ):
            self.assertIn(f'"$TMP_COPY/{forbidden_state}"', runner)

        self.assertIn(".runner_migrated as configured state", runner)
        self.assertIn("--exclude='./.runner_migrated'", runner)
        self.assertIn("--exclude='./.credentials_migrated'", runner)
        self.assertIn('cd "$RUNNER_DIR"', runner)
        self.assertIn("./config.sh", runner)
        self.assertNotIn('"$RUNNER_DIR/config.sh"', runner)
        self.assertIn("copied runner retained forbidden state", runner)
        self.assertIn(
            "github-tech-runner ALL=(root) NOPASSWD: "
            "/usr/local/sbin/hermes-tech-deploy-main *",
            helper_installer,
        )
        self.assertIn("PRODUCTION_CHANGED=false", helper_installer)
        self.assertIn("actions/runners/registration-token", activator)


if __name__ == "__main__":
    unittest.main()