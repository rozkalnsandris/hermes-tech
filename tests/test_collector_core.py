from __future__ import annotations

from hashlib import sha256
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import collector_core
import hermes_db

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sqlite"


class CollectorParsingTests(unittest.TestCase):
    def test_strip_html_collapses_markup_and_whitespace(self) -> None:
        value = "<p>Hello <strong>platform</strong></p>\n<div> team </div>"
        self.assertEqual(collector_core.strip_html(value), "Hello platform team")

    def test_entry_texts_prefers_content_and_falls_back_to_summary(self) -> None:
        entry = SimpleNamespace(
            content=[SimpleNamespace(value="<p>Full <b>content</b></p>")],
            summary="Summary only",
        )
        summary, full = collector_core.entry_texts(entry)
        self.assertEqual(full, "Full content")
        self.assertEqual(summary, "Full content")

        fallback = SimpleNamespace(content=[], summary="<p>Fallback summary</p>")
        summary, full = collector_core.entry_texts(fallback)
        self.assertEqual((summary, full), ("Fallback summary", "Fallback summary"))

    def test_entry_texts_applies_full_and_summary_limits(self) -> None:
        payload = "x" * (collector_core.MAX_CONTENT + 100)
        entry = SimpleNamespace(content=[SimpleNamespace(value=payload)], summary="")
        summary, full = collector_core.entry_texts(entry)
        self.assertEqual(len(summary), 500)
        self.assertEqual(len(full), collector_core.MAX_CONTENT)

    def test_entry_published_prefers_published_and_uses_updated_fallback(self) -> None:
        published = time.gmtime(1)
        updated = time.gmtime(2)
        with patch.object(collector_core.time, "mktime", side_effect=[1000, 2000]):
            value = collector_core.entry_published(
                SimpleNamespace(published_parsed=published, updated_parsed=updated)
            )
            self.assertEqual(value, "1970-01-01T00:16:40+00:00")

            value = collector_core.entry_published(
                SimpleNamespace(published_parsed=None, updated_parsed=updated)
            )
            self.assertEqual(value, "1970-01-01T00:33:20+00:00")

        self.assertEqual(collector_core.entry_published(SimpleNamespace()), "")


class CollectorSchemaStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.root = Path(self.tmp_obj.name)
        self.db = self.root / "data" / "hermes.db"
        self.log = self.root / "logs" / "collector.log"
        self.db.parent.mkdir(parents=True)

    def load_fixture(self, name: str) -> None:
        conn = sqlite3.connect(self.db)
        conn.executescript((FIXTURES / name).read_text(encoding="utf-8"))
        conn.commit()
        conn.close()

    def test_current_unversioned_database_is_not_implicitly_adopted(self) -> None:
        self.load_fixture("schema_v3_unversioned.sql")
        before = sha256(self.db.read_bytes()).hexdigest()
        with (
            patch.object(collector_core, "DB", self.db),
            patch.object(collector_core, "LOG", self.log),
        ):
            self.assertEqual(collector_core.main(), 1)
        self.assertEqual(sha256(self.db.read_bytes()).hexdigest(), before)
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(hermes_db.user_version(conn), 0)
        self.assertIn("preflight", self.log.read_text(encoding="utf-8"))

    def test_new_empty_database_is_initialized_to_current_schema(self) -> None:
        with patch.object(collector_core, "DB", self.db):
            conn = collector_core.open_database()
        self.addCleanup(conn.close)
        self.assertEqual(hermes_db.user_version(conn), 3)
        hermes_db.assert_schema(conn, 3)


class CollectorMainThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.root = Path(self.tmp_obj.name)
        self.db = self.root / "data" / "hermes.db"
        self.feeds = self.root / "feeds.txt"
        self.log = self.root / "logs" / "collector.log"

    def run_main(
        self,
        feed_lines: list[str],
        parsed_by_url: dict[str, object],
    ) -> int:
        self.feeds.write_text("\n".join(feed_lines) + "\n", encoding="utf-8")

        def fake_parse(url: str) -> object:
            result = parsed_by_url[url]
            if isinstance(result, BaseException):
                raise result
            return result

        with (
            patch.object(collector_core, "DB", self.db),
            patch.object(collector_core, "FEEDS", self.feeds),
            patch.object(collector_core, "LOG", self.log),
            patch.object(
                collector_core.feedparser,
                "parse",
                side_effect=fake_parse,
            ),
        ):
            return collector_core.main()

    @staticmethod
    def good_feed(link: str = "https://example.test/article") -> object:
        entry = SimpleNamespace(
            link=link,
            title="Useful release",
            summary="<p>Operational details</p>",
            content=[],
            published_parsed=None,
            updated_parsed=None,
        )
        return SimpleNamespace(bozo=False, entries=[entry])

    @staticmethod
    def failed_feed() -> object:
        return SimpleNamespace(
            bozo=True,
            entries=[],
            bozo_exception=RuntimeError("synthetic feed failure"),
        )

    def test_all_feed_failures_return_nonzero_and_are_recorded(self) -> None:
        rc = self.run_main(
            ["A|https://a.test/rss|devops", "B|https://b.test/rss|ai"],
            {
                "https://a.test/rss": self.failed_feed(),
                "https://b.test/rss": self.failed_feed(),
            },
        )
        self.assertEqual(rc, 1)
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        rows = conn.execute(
            "SELECT name, fetch_ok, fetch_fail FROM sources ORDER BY name"
        ).fetchall()
        self.assertEqual(rows, [("A", 0, 1), ("B", 0, 1)])
        self.assertEqual(hermes_db.user_version(conn), 3)

    def test_more_failures_than_successes_fail_closed(self) -> None:
        rc = self.run_main(
            [
                "A|https://a.test/rss|devops",
                "B|https://b.test/rss|ai",
                "C|https://c.test/rss|agents",
            ],
            {
                "https://a.test/rss": self.good_feed(),
                "https://b.test/rss": self.failed_feed(),
                "https://c.test/rss": self.failed_feed(),
            },
        )
        self.assertEqual(rc, 1)

    def test_equal_successes_and_failures_are_allowed(self) -> None:
        rc = self.run_main(
            ["A|https://a.test/rss|devops", "B|https://b.test/rss|ai"],
            {
                "https://a.test/rss": self.good_feed(),
                "https://b.test/rss": self.failed_feed(),
            },
        )
        self.assertEqual(rc, 0)

    def test_duplicate_article_link_is_idempotent(self) -> None:
        rc = self.run_main(
            ["A|https://a.test/rss|devops", "B|https://b.test/rss|devops"],
            {
                "https://a.test/rss": self.good_feed("https://same.test/item"),
                "https://b.test/rss": self.good_feed("https://same.test/item"),
            },
        )
        self.assertEqual(rc, 0)
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
