from __future__ import annotations

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sqlite3
import tempfile
import unittest

import digest_core
import hermes_db

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sqlite" / "schema_v3_unversioned.sql"
DOC = ROOT / "docs" / "sqlite-retention.md"
SPEC = spec_from_file_location(
    "sqlite_maintenance_tool",
    ROOT / "tools" / "sqlite_maintenance.py",
)
assert SPEC is not None and SPEC.loader is not None
sqlite_maintenance = module_from_spec(SPEC)
SPEC.loader.exec_module(sqlite_maintenance)


class SqliteMaintenanceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.root = Path(self.tmp_obj.name)
        self.db = self.root / "hermes.db"

        conn = sqlite3.connect(self.db)
        conn.executescript(FIXTURE.read_text(encoding="utf-8"))
        conn.execute("PRAGMA user_version = 3")
        conn.execute(
            """INSERT INTO articles(
                   id, source, title, link, published, summary, fetched_at,
                   digest_date, category, content, primary_category, topic_key,
                   routed_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                3,
                "Old",
                "Old published item",
                "https://example.test/old",
                "2026-04-01T00:00:00+00:00",
                "old summary",
                "2026-04-01T01:00:00+00:00",
                "2026-04-02",
                "devops",
                "x" * 1024,
                "devops",
                "old-item",
                "2026-04-01T02:00:00+00:00",
            ),
        )
        conn.execute(
            """INSERT INTO articles(
                   id, source, title, link, published, summary, fetched_at,
                   digest_date, category, content, primary_category, topic_key,
                   routed_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                4,
                "Fresh",
                "Fresh item",
                "https://example.test/fresh",
                None,
                "fresh summary",
                "2026-08-08T20:30:00+00:00",
                None,
                "ai",
                "fresh full content",
                "ai",
                "fresh-item",
                "2026-08-08T20:40:00+00:00",
            ),
        )
        conn.commit()
        conn.close()

    def test_report_is_read_only_and_reports_growth_retention_evidence(self) -> None:
        before = hermes_db.database_sha256(self.db)
        report = sqlite_maintenance.build_report(
            self.db,
            now=datetime(2026, 8, 8, 21, 0, tzinfo=timezone.utc),
        )
        after = hermes_db.database_sha256(self.db)

        self.assertEqual(after, before)
        self.assertEqual(report["mode"], "read-only-report")
        self.assertTrue(report["database"]["unchanged_during_report"])
        self.assertEqual(report["database"]["sha256"], before)
        self.assertEqual(report["database"]["schema_version"], 3)
        self.assertEqual(report["database"]["quick_check"], ["ok"])
        self.assertEqual(report["articles"]["total_rows"], 4)
        self.assertEqual(report["articles"]["published_rows"], 2)
        self.assertEqual(report["articles"]["rows_last_36h"], 1)
        self.assertEqual(report["articles"]["rows_last_7d"], 1)
        self.assertEqual(report["articles"]["rows_last_30d"], 1)
        self.assertEqual(
            report["articles"]["rows_with_content_older_than_review_age"],
            1,
        )
        self.assertGreaterEqual(
            report["articles"]["content_bytes_older_than_review_age"],
            1024,
        )
        self.assertFalse(report["review_required"])
        self.assertEqual(report["review_reasons"], [])
        self.assertEqual(report["policy"]["row_retention"], "indefinite")
        self.assertFalse(report["policy"]["automatic_row_deletion"])
        self.assertFalse(report["policy"]["automatic_content_pruning"])
        self.assertFalse(report["policy"]["automatic_vacuum"])

    def test_report_refuses_schema_drift_instead_of_repairing_it(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()
        before = hermes_db.database_sha256(self.db)

        with self.assertRaises(hermes_db.SchemaError):
            sqlite_maintenance.build_report(
                self.db,
                now=datetime(2026, 8, 8, 21, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(hermes_db.database_sha256(self.db), before)

    def test_policy_window_matches_digest_active_input_contract(self) -> None:
        self.assertEqual(sqlite_maintenance.ACTIVE_INPUT_HOURS, digest_core.FETCH_HOURS)

    def test_documented_policy_keeps_mutation_behind_separate_gate(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        required = (
            "retains article rows indefinitely",
            "There is no automatic row pruning",
            "older than 90 days",
            "256 MiB",
            "32 MiB",
            "20%",
            "128 MiB",
            "review triggers, not automatic actions",
            "separate production operation",
            "HERMES_TECH_DEPLOY_REQUIRED=no",
            "RPI5_MAIN_CHANGE_REQUIRED=no",
        )
        for marker in required:
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
