from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
OLD_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-main.yml"
POLLER = ROOT / "tools" / "pull-deploy" / "release" / "hermes-tech-pull-deploy"
HELPER = ROOT / "tools" / "pull-deploy" / "release" / "hermes-tech-deploy-main"
CLASSIFIER = ROOT / "tools" / "classify_deploy_impact.py"
READINESS = ROOT / "tools" / "pull-deploy" / "deploy_readiness.py"
INSTALLER = ROOT / "tools" / "pull-deploy" / "install-pull-deploy.sh"
ACTIVATOR = ROOT / "tools" / "pull-deploy" / "activate-pull-deploy.sh"
REMOVER = ROOT / "tools" / "pull-deploy" / "remove-self-hosted-runner.sh"
RETIRED_RECOVERY = ROOT / "tools" / "runner" / "recover-pending-digest-deadlock.sh"
SERVICE = ROOT / "ops" / "systemd" / "hermes-tech-pull-deploy.service"
TIMER = ROOT / "ops" / "systemd" / "hermes-tech-pull-deploy.timer"
DOC = ROOT / "docs" / "public-repository-hardening.md"

OLD_RUNNER_PATHS = (
    ROOT / "tools" / "runner" / "activate-github-main-deploy.sh",
    ROOT / "tools" / "runner" / "install-github-main-deploy.sh",
    ROOT / "tools" / "runner" / "install-github-tech-runner.sh",
    ROOT / "tools" / "runner" / "release" / "hermes-tech-deploy-main",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class GitHubMainDeployContractTests(unittest.TestCase):
    def test_public_repo_has_no_github_to_rpi5_runner_workflow(self) -> None:
        self.assertFalse(OLD_WORKFLOW.exists())
        for path in OLD_RUNNER_PATHS:
            self.assertFalse(path.exists(), path)

        ci = read(CI)
        self.assertIn("runs-on: ubuntu-24.04", ci)
        self.assertNotIn("self-hosted", ci)
        self.assertNotIn("hermes-tech-release", ci)

        uses_lines = [line.strip() for line in ci.splitlines() if "uses:" in line]
        self.assertTrue(uses_lines)
        for line in uses_lines:
            self.assertRegex(line, r"uses:\s+[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$")

    def test_poller_requires_exact_successful_ci_and_validate(self) -> None:
        text = read(POLLER)
        subprocess.run(["bash", "-n", str(POLLER)], check=True)

        for marker in (
            "actions/workflows/ci.yml/runs",
            'row.get("event") in {"push", "workflow_dispatch"}',
            'row.get("head_branch") == "main"',
            'row.get("head_sha") == sha',
            'row.get("conclusion") == "success"',
            'row.get("name") == "validate"',
            "WAIT_CI",
            "WAIT_CONTROL_PLANE_APPROVAL",
            "RUNTIME_ROLLOUT_REQUIRED",
            "DB_APPLY_REQUIRES_SEPARATE_APPROVAL",
            "DEPLOY_FAILED",
            "merge-base --is-ancestor",
            "sudo --non-interactive",
            "/usr/local/sbin/hermes-tech-deploy-main",
            "/usr/local/libexec/hermes-tech/classify-deploy-impact",
            "/usr/local/libexec/hermes-tech/deploy-readiness",
            "record_readiness",
        ):
            self.assertIn(marker, text)

        self.assertNotIn("git push", text)
        self.assertNotIn("git reset --hard", text)
        self.assertNotIn("production checkout is not clean", text)

    def test_control_plane_changes_use_canonical_classifier_and_exact_sha_activation(self) -> None:
        poller = read(POLLER)
        classifier = read(CLASSIFIER)
        activator = read(ACTIVATOR)
        doc = read(DOC)

        for marker in (
            'path.startswith(".github/workflows/")',
            'path.startswith("tools/pull-deploy/")',
            '"tools/ci.sh", "tools/classify_deploy_impact.py"',
            "hermes-tech-pull-deploy.service",
            "hermes-tech-pull-deploy.timer",
        ):
            self.assertIn(marker, classifier)

        self.assertIn("installed-control-plane-sha", poller)
        self.assertIn("approved-control-plane-sha", poller)
        self.assertIn("classify_range", poller)
        self.assertNotIn("mapfile -t changed_paths", poller)
        self.assertIn('printf \'%s\\n\' "$HEAD_SHA" >"$CONTROL_APPROVAL"', activator)
        self.assertIn("exact-SHA local approval", doc)

    def test_readiness_notifications_are_transition_aware_and_secret_safe(self) -> None:
        text = read(READINESS)
        self.assertIn("READINESS_STATE={'CHANGED' if changed else 'UNCHANGED'}", text)
        self.assertIn("TELEGRAM_BOT_TOKEN", text)
        self.assertIn("TELEGRAM_CHAT_ID", text)
        self.assertIn("[REDACTED]", text)
        self.assertIn("disable_web_page_preview", text)
        self.assertIn("production is not publish-ready", text)
        self.assertIn("production is current again", text)
        self.assertNotIn("requests", text)

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
            "pyproject.toml",
        ):
            self.assertIn(marker, text)

        self.assertNotIn("git push", text)
        self.assertNotIn("pip install", text)
        self.assertNotIn("sqlite_schema.py apply", text)
        self.assertNotIn("git checkout -B", text)
        self.assertNotIn("flock -n 9", text)

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
            r"^digests/([0-9]{4}-[0-9]{2}-[0-9]{2})-(devops|ai|agents)\.md$".replace("\\\\", "\\"),
            text,
        )
        self.assertIn("status=${line:0:2}", text)
        self.assertIn("[[ \"$status\" == '??' ]]", text)

    def test_one_time_deadlock_recovery_is_retired_fail_closed(self) -> None:
        text = read(RETIRED_RECOVERY)
        subprocess.run(["bash", "-n", str(RETIRED_RECOVERY)], check=True)
        self.assertIn("one-time 2026-08-07 recovery helper is retired", text)
        self.assertIn("issue #33", text)
        self.assertIn("public-repository-hardening.md", text)
        self.assertIn("exit 1", text)
        for forbidden in (
            "sudo ",
            "git reset",
            "git push",
            "digest.py",
            "systemctl",
        ):
            self.assertNotIn(forbidden, text)

    def test_installer_timer_and_removal_are_narrow(self) -> None:
        for script in (INSTALLER, ACTIVATOR, REMOVER):
            subprocess.run(["bash", "-n", str(script)], check=True)

        installer = read(INSTALLER)
        activator = read(ACTIVATOR)
        remover = read(REMOVER)
        service = read(SERVICE)
        timer = read(TIMER)

        self.assertIn(
            "andris ALL=(root) NOPASSWD: /usr/local/sbin/hermes-tech-deploy-main *",
            installer,
        )
        self.assertIn("/usr/local/libexec/hermes-tech", installer)
        self.assertIn("classify-deploy-impact", installer)
        self.assertIn("deploy-readiness", installer)
        self.assertIn("installed-control-plane-sha", installer)
        self.assertIn("PRODUCTION_CHANGED=false", installer)
        self.assertNotIn("systemctl enable --now", installer)

        self.assertIn("systemctl enable --now hermes-tech-pull-deploy.timer", activator)
        self.assertIn("277435981+rozkalnsandris@users.noreply.github.com", activator)
        self.assertIn("PUBLIC_SITE=PASS", activator)

        self.assertIn("User=andris", service)
        self.assertIn("ExecStart=/usr/local/sbin/hermes-tech-pull-deploy", service)
        self.assertIn("ProtectSystem=full", service)

        self.assertIn("OnUnitActiveSec=2min", timer)
        self.assertIn("RandomizedDelaySec=10s", timer)

        self.assertIn("actions/runners?per_page=100", remover)
        self.assertIn("--method DELETE", remover)
        self.assertIn("rpi5-hermes-tech-release", remover)
        self.assertIn("hermes-tech-pull-deploy.timer", remover)
        self.assertIn("production is not exact origin/main", remover)

    def test_activation_canary_precedes_recurring_timer(self) -> None:
        activator = read(ACTIVATOR)
        disable = "sudo systemctl disable --now hermes-tech-pull-deploy.timer"
        canary = "sudo systemctl start hermes-tech-pull-deploy.service"
        enable = "sudo systemctl enable --now hermes-tech-pull-deploy.timer"
        production_gate = "production did not reach the activated main SHA"
        public_gate = "https://tech.rozkalns.net/"

        for marker in (disable, canary, enable, production_gate, public_gate):
            self.assertIn(marker, activator)

        self.assertLess(activator.index(disable), activator.index(canary))
        self.assertLess(activator.index(canary), activator.index(production_gate))
        self.assertLess(activator.index(production_gate), activator.index(enable))
        self.assertLess(activator.index(public_gate), activator.index(enable))

    def test_systemd_service_preserves_narrow_sudo_transition(self) -> None:
        service = read(SERVICE)

        self.assertIn("User=andris", service)
        self.assertIn("NoNewPrivileges=false", service)
        self.assertIn("PrivateTmp=true", service)
        self.assertIn("ProtectSystem=full", service)
        self.assertIn("ProtectControlGroups=true", service)

        for forbidden in (
            "NoNewPrivileges=true",
            "DynamicUser=",
            "LockPersonality=",
            "MemoryDenyWriteExecute=",
            "PrivateDevices=",
            "ProtectClock=",
            "ProtectHostname=",
            "ProtectKernelLogs=",
            "ProtectKernelModules=",
            "ProtectKernelTunables=",
            "RestrictAddressFamilies=",
            "RestrictNamespaces=",
            "RestrictRealtime=",
            "RestrictSUIDSGID=",
            "SystemCallArchitectures=",
            "SystemCallFilter=",
            "SystemCallLog=",
        ):
            self.assertNotIn(forbidden, service)

    def test_documented_public_transition_is_fail_closed(self) -> None:
        text = read(DOC)
        for marker in (
            "no credential patterns",
            "not to rewrite Git history",
            "GitHub noreply identity",
            "deregister and remove",
            "all_external_contributors",
            "main` rulesets",
        ):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
