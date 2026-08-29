from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

import digest_pending


class PendingDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="hermes-pending-test-")
        self.root = Path(self.tmp.name)
        (self.root / "digests").mkdir()
        subprocess.run(
            ["git", "init", "-q", str(self.root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.db = self.root / "hermes.db"
        conn = sqlite3.connect(self.db)
        conn.execute(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY,
                primary_category TEXT,
                digest_date TEXT,
                topic_key TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO articles(id, primary_category, digest_date, topic_key) "
            "VALUES (?, ?, ?, ?)",
            [
                (10, "ai", None, "shared-topic"),
                (11, "ai", None, "shared-topic"),
                (12, "ai", None, "fresh-topic"),
                (20, "agents", None, "agent-topic"),
                (21, "agents", None, "agent-topic-new"),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_draft(
        self,
        relative: str,
        selected_ids: list[int],
    ) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = ",".join(map(str, selected_ids))
        path.write_text(
            f"<!-- selected_ids: {metadata} -->\n# test\n",
            encoding="utf-8",
        )

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db)

    def test_other_pending_draft_reserves_article_and_topic(self) -> None:
        self.write_draft("digests/2026-08-28-ai.md", [10])
        candidates = [
            {"id": 10, "topic_key": "shared-topic"},
            {"id": 11, "topic_key": "shared-topic"},
            {"id": 12, "topic_key": "fresh-topic"},
        ]
        conn = self.connect()
        try:
            filtered = digest_pending.filter_reserved_candidates(
                root=self.root,
                conn=conn,
                candidates=candidates,
                category="ai",
                digest_date="2026-08-29",
            )
        finally:
            conn.close()
        self.assertEqual(filtered, [{"id": 12, "topic_key": "fresh-topic"}])

    def test_current_draft_does_not_reserve_itself(self) -> None:
        self.write_draft("digests/2026-08-29-ai.md", [10])
        conn = self.connect()
        try:
            filtered = digest_pending.filter_reserved_candidates(
                root=self.root,
                conn=conn,
                candidates=[{"id": 10, "topic_key": "shared-topic"}],
                category="ai",
                digest_date="2026-08-29",
            )
        finally:
            conn.close()
        self.assertEqual(filtered, [{"id": 10, "topic_key": "shared-topic"}])

    def test_same_id_in_older_and_current_draft_is_reserved(self) -> None:
        self.write_draft("digests/2026-08-28-ai.md", [10])
        self.write_draft("digests/2026-08-29-ai.md", [10])
        conn = self.connect()
        try:
            filtered = digest_pending.filter_reserved_candidates(
                root=self.root,
                conn=conn,
                candidates=[{"id": 10, "topic_key": "shared-topic"}],
                category="ai",
                digest_date="2026-08-29",
            )
        finally:
            conn.close()
        self.assertEqual(filtered, [])

    def test_preflight_rejects_cross_draft_article_duplicate(self) -> None:
        self.write_draft("digests/2026-08-28-ai.md", [10])
        self.write_draft("digests/2026-08-29-ai.md", [10])
        conn = self.connect()
        try:
            with self.assertRaisesRegex(
                digest_pending.PendingDigestError,
                r"article_id 10 .*2026-08-28-ai\.md.*2026-08-29-ai\.md",
            ):
                digest_pending.validate_pending_drafts(self.root, conn)
        finally:
            conn.close()

    def test_preflight_rejects_cross_date_topic_duplicate_with_new_id(self) -> None:
        self.write_draft("digests/2026-08-28-ai.md", [10])
        self.write_draft("digests/2026-08-29-ai.md", [11])
        conn = self.connect()
        try:
            with self.assertRaisesRegex(
                digest_pending.PendingDigestError,
                r"topic_key ai/shared-topic .*2026-08-28-ai\.md.*2026-08-29-ai\.md",
            ):
                digest_pending.validate_pending_drafts(self.root, conn)
        finally:
            conn.close()

    def test_preflight_accepts_unique_pending_drafts(self) -> None:
        self.write_draft("digests/2026-08-28-ai.md", [10])
        self.write_draft("digests/2026-08-29-ai.md", [12])
        self.write_draft("digests/2026-08-29-agents.md", [20])
        conn = self.connect()
        try:
            state = digest_pending.validate_pending_drafts(self.root, conn)
        finally:
            conn.close()
        self.assertEqual(len(state.drafts), 3)
        self.assertEqual(set(state.id_owners), {10, 12, 20})

    def test_preflight_rejects_more_than_31_pending_dates(self) -> None:
        conn = sqlite3.connect(self.db)
        try:
            for index in range(32):
                article_id = 1000 + index
                date = f"2026-09-{index + 1:02d}"
                # Filename shape is the bounded contract under test; calendar
                # validity is intentionally not a second parser concern here.
                conn.execute(
                    "INSERT INTO articles VALUES (?, 'ai', NULL, ?)",
                    (article_id, f"topic-{index}"),
                )
                self.write_draft(
                    f"digests/{date}-ai.md",
                    [article_id],
                )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(
            digest_pending.PendingDigestError,
            r"32 > 31",
        ):
            digest_pending.collect_pending_drafts(self.root)

    def test_publish_wrapper_runs_pending_preflight_before_core(self) -> None:
        publish = Path(__file__).resolve().parents[1] / "publish.sh"
        text = publish.read_text(encoding="utf-8")
        preflight = text.index('"$PYTHON" "$PENDING_PREFLIGHT" validate')
        execution = text.index('exec bash -c "$PATCHED"')
        self.assertLess(preflight, execution)


if __name__ == "__main__":
    unittest.main(verbosity=2)
