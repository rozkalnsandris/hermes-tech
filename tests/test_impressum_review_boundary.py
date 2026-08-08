from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
IMPRESSUM = ROOT / "site" / "content" / "impressum.md"
HOW = ROOT / "site" / "content" / "how-hermes-works.md"
LLMS = ROOT / "site" / "static" / "llms.txt"
README = ROOT / "README.md"


def normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


class ImpressumReviewBoundaryTests(unittest.TestCase):
    def test_impressum_describes_automatic_validation_and_optional_human_review(self) -> None:
        text = normalized(IMPRESSUM)
        self.assertNotIn(
            "vor der Veröffentlichung durch einen Menschen geprüft",
            text,
        )
        self.assertIn(
            "durch technische Qualitäts-, Quellen- und Konsistenzprüfungen validiert",
            text,
        )
        self.assertIn(
            "ohne gesonderte manuelle Freigabe veröffentlicht werden",
            text,
        )
        self.assertIn("optionale inhaltliche Prüfung", text)
        self.assertIn("als KI-generiert gekennzeichnet", text)

    def test_public_surfaces_share_the_same_manual_approval_boundary(self) -> None:
        self.assertIn(
            "does **not** require Andris to approve every digest",
            HOW.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "Manual approval is not required for every run",
            LLMS.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "does not require a separate human approval for every run",
            README.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
