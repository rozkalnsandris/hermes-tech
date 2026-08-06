#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import unittest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "repository-hygiene.md"
REMOVED_HOST_PATHS = (
    "ops/bin/rpi5-backup",
    "ops/backup/rpi5-backup.conf.example",
    "ops/cron.d/rpi5-backup",
    "ops/logrotate.d/rpi5-backup",
)


class HostBackupOwnershipTests(unittest.TestCase):
    def tracked_paths(self) -> set[str]:
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return set(completed.stdout.splitlines())

    def test_host_wide_backup_implementation_is_not_tracked(self) -> None:
        tracked = self.tracked_paths()
        self.assertTrue(set(REMOVED_HOST_PATHS).isdisjoint(tracked))
        for relative in REMOVED_HOST_PATHS:
            self.assertFalse((REPO / relative).exists(), relative)

    def test_document_points_to_exact_infrastructure_source(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        required = (
            "rozkalnsandris/RPi5_main",
            "762174f12b72ad512600cfe2fc69bc80a530dadb",
            "RPi5_main` PR #28",
            "ops/backup/source-provenance.json",
            "194083f0d850c888d23f751aeb51e69a561a047a",
            "36b8223710fd2dbe90b6d69898ffc17c34285da1",
        )
        for marker in required:
            self.assertIn(marker, text)

    def test_all_removed_paths_are_documented(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        for relative in REMOVED_HOST_PATHS:
            self.assertIn(f"`{relative}`", text)

    def test_hermes_specific_backup_expectations_remain_documented(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        required = (
            "preserve the Git checkout and its history",
            "runtime `.env` only inside the encrypted host backup",
            "consistent SQLite snapshot of `data/hermes.db`",
            "`PRAGMA quick_check`",
            "exclude `venv/`, logs, runtime locks",
        )
        for marker in required:
            self.assertIn(marker, text)

    def test_production_boundary_is_explicit(self) -> None:
        text = " ".join(DOC.read_text(encoding="utf-8").split())
        required = (
            "does not read, compare, install, reload, execute, or change",
            "/usr/local/sbin/rpi5-backup",
            "/etc/rpi5-backup.conf",
            "separate explicit production approval",
            "no history is rewritten",
        )
        for marker in required:
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
