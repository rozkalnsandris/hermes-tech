from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIGEST_ENTRYPOINT = ROOT / "digest.py"
BOUNDARY_DOC = ROOT / "docs" / "digest-core-compatibility.md"


def imports_digest_core(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "digest_core" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == "digest_core":
            return True
    return False


class DiversityQuarantineTests(unittest.TestCase):
    def test_digest_entrypoint_is_the_only_root_production_import_of_digest_core(self) -> None:
        direct_importers = []
        for path in sorted(ROOT.glob("*.py")):
            if path.name == "digest_core.py":
                continue
            if imports_digest_core(path):
                direct_importers.append(path.name)
        self.assertEqual(direct_importers, ["digest.py"])

    def test_supported_entrypoint_exports_authoritative_diversity_owners(self) -> None:
        env = os.environ.copy()
        env["HERMES_TECH_ROOT"] = str(ROOT)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import digest; "
                    "print(digest._core.diversity_filter.__module__); "
                    "print(digest._core._resolve_digest_selected_ids.__module__)"
                ),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), ["digest_diversity", "digest_diversity"])

    def test_runtime_guard_fails_closed_on_wrong_diversity_owner(self) -> None:
        source = DIGEST_ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn('installed.__module__ != "digest_diversity"', source)
        self.assertIn("diversity contract installation failed closed", source)
        self.assertLess(
            source.index("\n_install_diversity_contracts()\n"),
            source.index("\n_export_core_api()\n"),
        )

    def test_boundary_document_names_authoritative_module_and_import_rule(self) -> None:
        text = " ".join(BOUNDARY_DOC.read_text(encoding="utf-8").split())
        self.assertIn(
            "`digest_diversity.py` is the only authoritative production implementation",
            text,
        )
        self.assertIn(
            "Production Python modules in this repository must not import `digest_core` directly",
            text,
        )
        self.assertIn("startup fails closed", text)
        self.assertIn("HERMES_TECH_DEPLOY_REQUIRED=yes", text)
        self.assertIn("RPI5_MAIN_CHANGE_REQUIRED=no", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
