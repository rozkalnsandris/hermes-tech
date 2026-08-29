from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools" / "pull-deploy" / "release" / "hermes-tech-deploy-main"


class PendingDigestBacklogContractTests(unittest.TestCase):
    def test_multi_day_backlog_is_bounded_and_byte_preserving(self) -> None:
        text = HELPER.read_text(encoding="utf-8")
        subprocess.run(["bash", "-n", str(HELPER)], check=True)

        for marker in (
            "declare -A PENDING_DIGEST_DATES=()",
            "MAX_PENDING_DIGEST_DATES=31",
            'PENDING_DIGEST_DATES["$digest_date"]=1',
            "pending digest backlog spans too many dates",
            "production checkout has tracked or staged change",
            "production checkout has unrelated untracked path",
            "target commit already tracks pending digest path",
            "pending digest bytes changed during deploy",
            "pending digest set changed during deploy",
            "pending-digests.sha256",
            "sha256sum",
        ):
            self.assertIn(marker, text)

        self.assertNotIn(
            '[[ -z "$PENDING_DIGEST_DATE" || "$PENDING_DIGEST_DATE" == "$digest_date" ]]',
            text,
        )
        self.assertNotIn("PENDING_DIGEST_DATE=$digest_date", text)
        self.assertNotIn("(( ${#PENDING_DIGEST_PATHS[@]} <= 4 ))", text)

    def test_generated_digest_allowlist_stays_narrow(self) -> None:
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn(
            r"^digests/([0-9]{4}-[0-9]{2}-[0-9]{2})-(devops|ai|agents)\.md$".replace("\\\\", "\\"),
            text,
        )
        self.assertIn(
            r"^digests/([0-9]{4}-[0-9]{2}-[0-9]{2})\.md$".replace("\\\\", "\\"),
            text,
        )
        self.assertIn("[[ \"$status\" == '??' ]]", text)


if __name__ == "__main__":
    unittest.main()
