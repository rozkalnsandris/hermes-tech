from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "classify_deploy_impact.py"
SPEC = importlib.util.spec_from_file_location("classify_deploy_impact", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DeployImpactPathTests(unittest.TestCase):
    def test_no_paths_is_no_deploy(self) -> None:
        impact = MODULE.classify_paths([])
        self.assertEqual(impact.classification, MODULE.NO_DEPLOY)
        self.assertFalse(impact.deploy_required)

    def test_docs_and_tests_are_no_deploy(self) -> None:
        impact = MODULE.classify_paths([
            "README.md",
            "docs/runbook.md",
            "tests/test_example.py",
            ".github/ISSUE_TEMPLATE/bug.md",
        ])
        self.assertEqual(impact.classification, MODULE.NO_DEPLOY)
        self.assertFalse(impact.deploy_required)
        self.assertEqual(impact.auto_deploy_paths, ())

    def test_site_or_runtime_code_defaults_to_auto_deploy_safe(self) -> None:
        impact = MODULE.classify_paths([
            "collector_core.py",
            "site/layouts/_default/baseof.html",
        ])
        self.assertEqual(impact.classification, MODULE.AUTO_DEPLOY_SAFE)
        self.assertTrue(impact.deploy_required)
        self.assertEqual(
            impact.auto_deploy_paths,
            ("collector_core.py", "site/layouts/_default/baseof.html"),
        )

    def test_generated_content_is_auto_deploy_safe_not_manual_rollout(self) -> None:
        impact = MODULE.classify_paths([
            "digests/2026-08-09-devops.md",
            "site/content/digest/2026-08-09.md",
            "site/static/og/2026-08-09-devops.png",
        ])
        self.assertEqual(impact.classification, MODULE.AUTO_DEPLOY_SAFE)
        self.assertFalse(impact.runtime_changed)
        self.assertFalse(impact.control_plane_changed)
        self.assertFalse(impact.db_sensitive_changed)

    def test_control_plane_requires_exact_sha_approval(self) -> None:
        impact = MODULE.classify_paths([
            ".github/workflows/ci.yml",
            "tools/pull-deploy/release/hermes-tech-pull-deploy",
        ])
        self.assertEqual(
            impact.classification,
            MODULE.CONTROL_PLANE_APPROVAL_REQUIRED,
        )
        self.assertTrue(impact.control_plane_changed)

    def test_runtime_is_stronger_than_control_plane(self) -> None:
        impact = MODULE.classify_paths([
            ".github/workflows/ci.yml",
            ".python-version",
            "pyproject.toml",
            "requirements-bootstrap.txt",
            "requirements.txt",
        ])
        self.assertEqual(impact.classification, MODULE.RUNTIME_ROLLOUT_REQUIRED)
        self.assertTrue(impact.runtime_changed)
        self.assertTrue(impact.control_plane_changed)
        self.assertFalse(impact.db_sensitive_changed)

    def test_every_root_requirements_manifest_is_runtime_sensitive(self) -> None:
        impact = MODULE.classify_paths([
            "requirements-dev.txt",
            "requirements-bootstrap.txt",
        ])
        self.assertEqual(impact.classification, MODULE.RUNTIME_ROLLOUT_REQUIRED)
        self.assertEqual(
            impact.runtime_paths,
            ("requirements-bootstrap.txt", "requirements-dev.txt"),
        )

    def test_db_sensitive_is_strongest_class(self) -> None:
        impact = MODULE.classify_paths([
            ".python-version",
            "hermes_db.py",
            "tools/sqlite_schema.py",
        ])
        self.assertEqual(
            impact.classification,
            MODULE.DB_APPLY_REQUIRES_SEPARATE_APPROVAL,
        )
        self.assertTrue(impact.runtime_changed)
        self.assertTrue(impact.db_sensitive_changed)

    def test_incident_115_sensitive_set_classifies_manual_runtime(self) -> None:
        impact = MODULE.classify_paths([
            ".github/workflows/ci.yml",
            ".python-version",
            "pyproject.toml",
            "requirements-bootstrap.txt",
            "requirements.txt",
        ])
        self.assertEqual(impact.classification, MODULE.RUNTIME_ROLLOUT_REQUIRED)
        self.assertEqual(impact.runtime_paths, (
            ".python-version",
            "pyproject.toml",
            "requirements-bootstrap.txt",
            "requirements.txt",
        ))
        self.assertEqual(impact.control_plane_paths, (".github/workflows/ci.yml",))

    def test_unknown_path_fails_toward_auto_deploy_not_no_deploy(self) -> None:
        impact = MODULE.classify_paths(["future_runtime_contract.toml"])
        self.assertEqual(impact.classification, MODULE.AUTO_DEPLOY_SAFE)
        self.assertTrue(impact.deploy_required)

    def test_unsafe_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.classify_paths(["../outside"])


class DeployImpactGitTests(unittest.TestCase):
    def test_exact_git_diff_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            self._git(root, "init", "-q")
            self._git(root, "config", "user.name", "Test")
            self._git(root, "config", "user.email", "test@example.invalid")
            (root / "README.md").write_text("one\n", encoding="utf-8")
            self._git(root, "add", "README.md")
            self._git(root, "commit", "-q", "-m", "base")
            base = self._git(root, "rev-parse", "HEAD").strip()

            (root / ".python-version").write_text("3.11.15\n", encoding="utf-8")
            self._git(root, "add", ".python-version")
            self._git(root, "commit", "-q", "-m", "runtime")
            target = self._git(root, "rev-parse", "HEAD").strip()

            previous = Path.cwd()
            try:
                import os
                os.chdir(root)
                base_sha, target_sha, paths = MODULE.changed_paths_between(base, target)
            finally:
                os.chdir(previous)

            self.assertEqual(base_sha, base)
            self.assertEqual(target_sha, target)
            self.assertEqual(paths, (".python-version",))
            self.assertEqual(
                MODULE.classify_paths(paths).classification,
                MODULE.RUNTIME_ROLLOUT_REQUIRED,
            )

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        import subprocess

        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return proc.stdout


if __name__ == "__main__":
    unittest.main()
