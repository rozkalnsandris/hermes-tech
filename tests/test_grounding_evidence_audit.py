from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "grounding-evidence-audit.md"
SPEC = spec_from_file_location(
    "grounding_evidence_audit_tool",
    ROOT / "tools" / "audit_grounding_evidence.py",
)
assert SPEC is not None and SPEC.loader is not None
audit = module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def file_sha(path: Path) -> str:
    digest = sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


class GroundingEvidenceAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory(prefix="hermes-grounding-audit-")
        self.addCleanup(self.tmp_obj.cleanup)
        self.runtime = Path(self.tmp_obj.name)
        self.digests = self.runtime / "digests"
        self.data = self.runtime / "data"
        self.digests.mkdir()
        self.data.mkdir()
        self.db = self.data / "hermes.db"

        (self.digests / "2026-08-08-devops.md").write_text(
            "<!-- selected_ids: 1,2 -->\n# DevOps fixture\n",
            encoding="utf-8",
        )
        (self.digests / "2026-08-08-ai.md").write_text(
            "<!-- selected_ids: 3 -->\n# AI fixture\n",
            encoding="utf-8",
        )
        (self.digests / "2026-06-01-agents.md").write_text(
            "<!-- selected_ids: 999 -->\n# Outside audit window\n",
            encoding="utf-8",
        )

        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL UNIQUE,
                published TEXT,
                summary TEXT,
                fetched_at TEXT NOT NULL,
                digest_date TEXT,
                category TEXT DEFAULT 'devops',
                content TEXT,
                primary_category TEXT,
                topic_key TEXT,
                routed_at TEXT
            );
            CREATE TABLE sources (
                name TEXT PRIMARY KEY,
                fetch_ok INTEGER DEFAULT 0,
                fetch_fail INTEGER DEFAULT 0,
                collected INTEGER DEFAULT 0,
                picked INTEGER DEFAULT 0
            );
            PRAGMA user_version = 3;
            """
        )
        rows = [
            (
                1,
                "Short Feed",
                "Short",
                "https://example.test/short",
                "s" * 200,
                "2026-08-08T08:00:00+00:00",
                "2026-08-08",
                "c" * 200,
            ),
            (
                2,
                "Medium Feed",
                "Medium",
                "https://example.test/medium",
                "s" * 500,
                "2026-08-08T09:00:00+00:00",
                "2026-08-08",
                "LIMITATION_SENTINEL_" + "m" * 980,
            ),
            (
                3,
                "Long Feed",
                "Long",
                "https://example.test/long",
                "s" * 500,
                "2026-08-08T10:00:00+00:00",
                "2026-08-08",
                "PRIVATE_TEXT_SENTINEL_" + "l" * 1980,
            ),
        ]
        conn.executemany(
            """INSERT INTO articles(
                   id, source, title, link, summary, fetched_at, digest_date, content
               ) VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        conn.close()

    def test_report_measures_extra_evidence_without_mutating_or_emitting_text(self) -> None:
        before = file_sha(self.db)
        report = audit.build_report(
            self.runtime,
            days=30,
            max_digests=90,
            now=datetime(2026, 8, 8, 21, 0, tzinfo=timezone.utc),
        )
        after = file_sha(self.db)

        self.assertEqual(after, before)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["digest_files_sampled"], 2)
        self.assertEqual(report["selected_id_references"], 3)
        self.assertEqual(report["unique_selected_ids"], 3)
        self.assertEqual(report["rows"]["found"], 3)
        self.assertEqual(report["rows"]["missing_ids"], [])
        self.assertEqual(report["evidence_depth"]["rows_with_content_beyond_300"], 2)
        self.assertEqual(report["evidence_depth"]["rows_with_content_beyond_1200"], 1)
        self.assertEqual(
            report["evidence_depth"]["total_additional_chars_if_excerpt_raised_to_1200"],
            1600,
        )
        self.assertEqual(
            report["evidence_depth"]["approx_additional_english_input_tokens_at_1200"],
            480,
        )
        self.assertTrue(report["database"]["unchanged_during_audit"])
        self.assertFalse(report["privacy"]["article_text_emitted"])
        self.assertFalse(report["privacy"]["article_urls_emitted"])
        self.assertFalse(report["privacy"]["model_or_network_call_performed"])

        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("LIMITATION_SENTINEL", encoded)
        self.assertNotIn("PRIVATE_TEXT_SENTINEL", encoded)
        self.assertNotIn("https://example.test", encoded)

    def test_missing_selected_database_row_is_reported_incomplete(self) -> None:
        (self.digests / "2026-08-08-agents.md").write_text(
            "<!-- selected_ids: 404 -->\n# Missing fixture\n",
            encoding="utf-8",
        )
        report = audit.build_report(
            self.runtime,
            days=30,
            now=datetime(2026, 8, 8, 21, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["rows"]["missing_ids"], [404])

    def test_duplicate_selected_ids_in_one_digest_fail_closed(self) -> None:
        path = self.digests / "2026-08-08-devops.md"
        path.write_text(
            "<!-- selected_ids: 1,1 -->\n# Duplicate fixture\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(audit.GroundingAuditError, "invalid or duplicated"):
            audit.build_report(
                self.runtime,
                days=30,
                now=datetime(2026, 8, 8, 21, 0, tzinfo=timezone.utc),
            )

    def test_policy_keeps_production_prompt_change_behind_real_audit(self) -> None:
        text = " ".join(DOC.read_text(encoding="utf-8").split())
        for marker in (
            "measurement and decision gate",
            "1,200-character value is only a **review candidate**",
            "does **not**",
            "fetch canonical article webpages",
            "change `fetch_routed_candidates()` output",
            "context caching on disk is enabled by default",
            "HERMES_TECH_DEPLOY_REQUIRED=no",
            "RPI5_MAIN_CHANGE_REQUIRED=no",
        ):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
