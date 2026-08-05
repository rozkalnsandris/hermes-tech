from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import hermes_db

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sqlite"


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class MigrationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.root = Path(self.tmp_obj.name)

    def database_from_fixture(self, name: str) -> Path:
        path = self.root / f"{name}.sqlite3"
        conn = sqlite3.connect(path)
        try:
            conn.executescript((FIXTURES / name).read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        return path

    @staticmethod
    def rows(conn: sqlite3.Connection) -> tuple[tuple, tuple]:
        articles = tuple(
            conn.execute(
                """SELECT id, source, title, link, published, summary,
                          fetched_at, digest_date
                   FROM articles ORDER BY id"""
            ).fetchall()
        )
        sources = tuple(
            conn.execute(
                """SELECT name, fetch_ok, fetch_fail, collected, picked
                   FROM sources ORDER BY name"""
            ).fetchall()
        )
        return articles, sources

    def test_v1_upgrades_once_and_preserves_rows_and_counters(self) -> None:
        path = self.database_from_fixture("schema_v1.sql")
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        before = self.rows(conn)

        self.assertEqual(
            hermes_db.migrate_to_current(conn),
            (
                "adopt-legacy-v1",
                "v1->v2:add-category-and-content",
                "v2->v3:add-routing-columns",
            ),
        )
        self.assertEqual(hermes_db.user_version(conn), 3)
        hermes_db.assert_schema(conn, 3)
        self.assertEqual(hermes_db.quick_check(conn), ("ok",))
        self.assertEqual(self.rows(conn), before)
        self.assertEqual(
            conn.execute(
                "SELECT category, content FROM articles ORDER BY id"
            ).fetchall(),
            [("devops", None), ("devops", None)],
        )
        self.assertEqual(hermes_db.migrate_to_current(conn), ())
        self.assertEqual(self.rows(conn), before)

    def test_v2_and_unversioned_current_upgrade_paths(self) -> None:
        v2 = self.database_from_fixture("schema_v2.sql")
        conn2 = sqlite3.connect(v2)
        self.addCleanup(conn2.close)
        enrichment = tuple(
            conn2.execute(
                "SELECT id, category, content FROM articles ORDER BY id"
            ).fetchall()
        )
        self.assertEqual(
            hermes_db.migrate_to_current(conn2),
            ("adopt-legacy-v2", "v2->v3:add-routing-columns"),
        )
        self.assertEqual(
            tuple(
                conn2.execute(
                    "SELECT id, category, content FROM articles ORDER BY id"
                ).fetchall()
            ),
            enrichment,
        )

        v3 = self.database_from_fixture("schema_v3_unversioned.sql")
        conn3 = sqlite3.connect(v3)
        self.addCleanup(conn3.close)
        schema_before = tuple(
            conn3.execute(
                "SELECT type, name, tbl_name, sql "
                "FROM sqlite_master ORDER BY type, name"
            ).fetchall()
        )
        rows_before = self.rows(conn3)
        self.assertEqual(
            hermes_db.migrate_to_current(conn3),
            ("adopt-legacy-v3",),
        )
        self.assertEqual(hermes_db.user_version(conn3), 3)
        self.assertEqual(
            tuple(
                conn3.execute(
                    "SELECT type, name, tbl_name, sql "
                    "FROM sqlite_master ORDER BY type, name"
                ).fetchall()
            ),
            schema_before,
        )
        self.assertEqual(self.rows(conn3), rows_before)

    def test_empty_initializes_but_existing_legacy_requires_apply(self) -> None:
        empty = sqlite3.connect(self.root / "empty.sqlite3")
        self.addCleanup(empty.close)
        hermes_db.ensure_current_schema(empty, allow_initialize=True)
        self.assertEqual(hermes_db.user_version(empty), 3)
        hermes_db.assert_schema(empty, 3)

        old_path = self.database_from_fixture("schema_v1.sql")
        old = sqlite3.connect(old_path)
        self.addCleanup(old.close)
        with self.assertRaisesRegex(
            hermes_db.SchemaUpgradeRequired,
            "preflight",
        ):
            hermes_db.ensure_current_schema(old, allow_initialize=True)
        self.assertEqual(hermes_db.user_version(old), 0)

    def test_partial_or_constraint_broken_schema_fails_closed(self) -> None:
        partial = sqlite3.connect(self.root / "partial.sqlite3")
        self.addCleanup(partial.close)
        partial.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL,
                title TEXT NOT NULL, link TEXT NOT NULL UNIQUE,
                published TEXT, summary TEXT, fetched_at TEXT NOT NULL,
                digest_date TEXT, category TEXT DEFAULT 'devops'
            );
            CREATE TABLE sources (
                name TEXT PRIMARY KEY, fetch_ok INTEGER DEFAULT 0,
                fetch_fail INTEGER DEFAULT 0, collected INTEGER DEFAULT 0,
                picked INTEGER DEFAULT 0
            );
            CREATE INDEX idx_articles_fetched ON articles(fetched_at);
            """
        )
        partial.commit()
        before = tuple(
            partial.execute("SELECT sql FROM sqlite_master ORDER BY name")
        )
        with self.assertRaises(hermes_db.UnexpectedSchemaError):
            hermes_db.migrate_to_current(partial)
        self.assertEqual(hermes_db.user_version(partial), 0)
        self.assertEqual(
            tuple(partial.execute("SELECT sql FROM sqlite_master ORDER BY name")),
            before,
        )

        no_unique = sqlite3.connect(self.root / "no-unique.sqlite3")
        self.addCleanup(no_unique.close)
        no_unique.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL,
                title TEXT NOT NULL, link TEXT NOT NULL, published TEXT,
                summary TEXT, fetched_at TEXT NOT NULL, digest_date TEXT
            );
            CREATE TABLE sources (
                name TEXT PRIMARY KEY, fetch_ok INTEGER DEFAULT 0,
                fetch_fail INTEGER DEFAULT 0, collected INTEGER DEFAULT 0,
                picked INTEGER DEFAULT 0
            );
            CREATE INDEX idx_articles_fetched ON articles(fetched_at);
            """
        )
        no_unique.commit()
        with self.assertRaisesRegex(
            hermes_db.UnexpectedSchemaError,
            "UNIQUE",
        ):
            hermes_db.migrate_to_current(no_unique)

    def test_sql_readonly_and_lock_errors_are_not_swallowed(self) -> None:
        path = self.database_from_fixture("schema_v1.sql")
        conn = sqlite3.connect(path)
        self.addCleanup(conn.close)
        broken = hermes_db.Migration(
            1,
            2,
            "broken",
            (
                "ALTER TABLE articles ADD COLUMN category TEXT",
                "THIS IS NOT SQL",
            ),
        )
        with patch.dict(hermes_db.MIGRATIONS, {1: broken}):
            with self.assertRaises(sqlite3.OperationalError):
                hermes_db.migrate_to_current(conn)
        self.assertEqual(hermes_db.user_version(conn), 0)
        self.assertNotIn(
            "category",
            [row[1] for row in conn.execute("PRAGMA table_info(articles)")],
        )

        readonly = hermes_db.open_readonly(path)
        self.addCleanup(readonly.close)
        with self.assertRaises(sqlite3.OperationalError):
            hermes_db.migrate_to_current(readonly)

        locker = sqlite3.connect(path, timeout=0)
        contender = sqlite3.connect(path, timeout=0)
        self.addCleanup(locker.close)
        self.addCleanup(contender.close)
        locker.execute("BEGIN EXCLUSIVE")
        with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
            hermes_db.migrate_to_current(contender)
        locker.rollback()

    def test_preflight_is_read_only_and_reports_exact_plan(self) -> None:
        path = self.database_from_fixture("schema_v1.sql")
        before = file_hash(path)
        report = hermes_db.preflight(path)
        self.assertEqual(file_hash(path), before)
        self.assertTrue(report["read_only_unchanged"])
        self.assertEqual(report["user_version"], 0)
        self.assertEqual(report["inferred_legacy_version"], 1)
        self.assertEqual(report["quick_check"], ["ok"])
        self.assertEqual(report["articles"]["row_count"], 2)
        self.assertEqual(report["sources"]["fetch_ok"], 11)
        self.assertEqual(
            report["steps"],
            [
                "adopt-legacy-v1",
                "v1->v2:add-category-and-content",
                "v2->v3:add-routing-columns",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
