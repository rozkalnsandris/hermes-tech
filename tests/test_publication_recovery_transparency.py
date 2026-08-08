from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ROOT / "publish_core.sh"
HOW = ROOT / "site" / "content" / "how-hermes-works.md"
TRANSPARENCY = ROOT / "docs" / "transparency-contract.md"


def normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


class PublicationRecoveryTransparencyTests(unittest.TestCase):
    def test_executable_contract_has_pre_db_rollback_and_post_db_recovery(self) -> None:
        source = PUBLISH.read_text(encoding="utf-8")
        self.assertIn("DB_COMMITTED=0", source)
        self.assertIn("rc != 0 && DB_COMMITTED == 0", source)
        self.assertIn("restore_files", source)
        self.assertIn("DB_COMMITTED=1", source)
        self.assertIn("exit 76", source)
        self.assertIn(
            "lapa un DB jau ir atjauninātas, GitHub sinhronizācija nav veikta",
            source,
        )
        self.assertIn(
            "publicētā lapa un DB saglabātas, lokālais commits",
            source,
        )

    def test_public_page_no_longer_claims_blanket_rollback(self) -> None:
        text = normalized(HOW)
        self.assertNotIn(
            "rolls files, database state, and Git state back on failure",
            text,
        )
        self.assertIn(
            "Before the publication database update is committed, a failure restores",
            text,
        )
        self.assertIn(
            "If the later Git synchronization fails, Hermes reports a recovery state",
            text,
        )
        self.assertIn(
            "instead of rolling the already-live page and database back",
            text,
        )

    def test_transparency_contract_names_both_recovery_phases(self) -> None:
        text = normalized(TRANSPARENCY)
        self.assertIn("Publication and recovery", text)
        self.assertIn("Before the publication database update is committed", text)
        self.assertIn("Git synchronization recovery state", text)
        self.assertIn("dedicated recovery exit code", text)
        self.assertIn("post-database Git failures as pending synchronization/recovery", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
