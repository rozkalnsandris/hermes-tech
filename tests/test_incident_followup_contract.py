from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "incidents" / "2026-08-08-recovery-followup.md"


class IncidentFollowupContractTests(unittest.TestCase):
    def test_remote_ahead_guard_is_documented_as_fail_closed(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("REMOTE_AHEAD", text)
        self.assertIn("must remain fail-closed", text)
        self.assertIn("auto-merging, rebasing, or force-pushing", text)


if __name__ == "__main__":
    unittest.main()
