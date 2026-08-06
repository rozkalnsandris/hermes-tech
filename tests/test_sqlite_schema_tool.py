from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import hermes_db

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "sqlite" / "schema_v1.sql"
SPEC = spec_from_file_location(
    "sqlite_schema_tool",
    ROOT / "tools" / "sqlite_schema.py",
)
assert SPEC is not None and SPEC.loader is not None
sqlite_schema_tool = module_from_spec(SPEC)
SPEC.loader.exec_module(sqlite_schema_tool)


class SqliteSchemaToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.root = Path(self.tmp_obj.name)
        self.db = self.root / "hermes.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(FIXTURE.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()

    def test_sha_bound_apply_produces_backup_and_evidence(self) -> None:
        before_sha = hermes_db.database_sha256(self.db)
        before = sqlite_schema_tool.build_preflight(self.db)
        evidence_path = self.root / "evidence" / "apply.json"
        evidence = sqlite_schema_tool.apply_migration(
            self.db,
            expected_sha256=before_sha,
            backup_dir=self.root / "backups",
            evidence_path=evidence_path,
        )
        self.assertEqual(evidence["status"], "success")
        self.assertEqual(evidence["before"]["sha256"], before["sha256"])
        self.assertEqual(evidence["before"]["steps"], before["steps"])
        self.assertEqual(evidence["backup_sha256"], before_sha)
        backup = Path(evidence["backup_path"])
        self.assertTrue(backup.is_file())
        self.assertEqual(hermes_db.database_sha256(backup), before_sha)
        self.assertEqual(evidence["after"]["user_version"], 3)
        self.assertFalse(evidence["after"]["needs_change"])
        self.assertEqual(
            json.loads(evidence_path.read_text(encoding="utf-8"))["status"],
            "success",
        )
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(hermes_db.user_version(conn), 3)
        self.assertEqual(
            conn.execute(
                "SELECT name, fetch_ok, fetch_fail, collected, picked "
                "FROM sources ORDER BY name"
            ).fetchall(),
            [("Alpha", 7, 1, 12, 2), ("Beta", 4, 3, 9, 1)],
        )

    def test_wrong_hash_and_sidecar_block_before_write(self) -> None:
        with self.assertRaisesRegex(hermes_db.SchemaError, "nesakrīt"):
            sqlite_schema_tool.apply_migration(
                self.db,
                expected_sha256="0" * 64,
                backup_dir=self.root / "backups",
                evidence_path=self.root / "wrong.json",
            )
        self.assertFalse((self.root / "backups").exists())
        self.assertFalse((self.root / "wrong.json").exists())

        Path(str(self.db) + "-wal").write_bytes(b"synthetic")
        with self.assertRaisesRegex(hermes_db.SchemaError, "sidecar"):
            sqlite_schema_tool.apply_migration(
                self.db,
                expected_sha256=hermes_db.database_sha256(self.db),
                backup_dir=self.root / "backups",
                evidence_path=self.root / "sidecar.json",
            )

    def test_failed_sql_writes_failure_evidence_and_rolls_back(self) -> None:
        broken = hermes_db.Migration(
            1,
            2,
            "broken",
            (
                "ALTER TABLE articles ADD COLUMN category TEXT",
                "NOT VALID SQL",
            ),
        )
        evidence_path = self.root / "failure.json"
        with patch.dict(hermes_db.MIGRATIONS, {1: broken}):
            with self.assertRaises(sqlite3.OperationalError):
                sqlite_schema_tool.apply_migration(
                    self.db,
                    expected_sha256=hermes_db.database_sha256(self.db),
                    backup_dir=self.root / "backups",
                    evidence_path=evidence_path,
                )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["error_type"], "OperationalError")
        self.assertTrue(Path(evidence["backup_path"]).is_file())
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(hermes_db.user_version(conn), 0)
        self.assertNotIn(
            "category",
            [row[1] for row in conn.execute("PRAGMA table_info(articles)")],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
