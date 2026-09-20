from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACTIVATOR = ROOT / "tools" / "pull-deploy" / "activate-pull-deploy.sh"
INSTALLER = ROOT / "tools" / "pull-deploy" / "install-pull-deploy.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ControlPlaneActivationFailClosedTests(unittest.TestCase):
    def test_activator_stops_without_ignoring_post_mutation_errors(self) -> None:
        text = read(ACTIVATOR)
        subprocess.run(["bash", "-n", str(ACTIVATOR)], check=True)

        for marker in (
            "MUTATION_STARTED=false",
            "MUTATION_STARTED=true",
            "trap finish EXIT",
            "PULL_DEPLOY_ACTIVATION_RESULT=FAIL_STOP_NO_ROLLBACK",
            "ROLLBACK_PERFORMED=false",
            "AUTOMATIC_CLEANUP_PERFORMED=false",
            "DATABASE_MIGRATIONS_EXECUTED=false",
        ):
            self.assertIn(marker, text)

        self.assertNotIn(
            "systemctl disable --now hermes-tech-pull-deploy.timer >/dev/null 2>&1 || true",
            text,
        )
        self.assertNotIn(
            "systemctl reset-failed hermes-tech-pull-deploy.service >/dev/null 2>&1 || true",
            text,
        )
        self.assertIn(
            "if systemctl is-failed --quiet hermes-tech-pull-deploy.service; then\n"
            "    sudo systemctl reset-failed hermes-tech-pull-deploy.service >/dev/null 2>&1\n"
            "fi\n"
            "sudo systemctl start hermes-tech-pull-deploy.service",
            text,
        )

    def test_installer_preserves_failure_workdir_after_mutation(self) -> None:
        text = read(INSTALLER)
        subprocess.run(["bash", "-n", str(INSTALLER)], check=True)

        for marker in (
            "MUTATION_STARTED=false",
            "MUTATION_STARTED=true",
            "trap finish EXIT",
            "PULL_DEPLOY_INSTALL_RESULT=FAIL_STOP_NO_ROLLBACK",
            "FAILURE_WORKDIR_PRESERVED=",
            "ROLLBACK_PERFORMED=false",
            "AUTOMATIC_CLEANUP_PERFORMED=false",
            "PRODUCTION_CHANGED=false",
            "DATABASE_MIGRATIONS_AUTHORIZED=false",
            'if [[ $rc -ne 0 && "$MUTATION_STARTED" == true ]]',
            'elif [[ -n "$TMPDIR_INSTALL" ]]',
        ):
            self.assertIn(marker, text)

        self.assertNotIn("cleanup()", text)


if __name__ == "__main__":
    unittest.main()
