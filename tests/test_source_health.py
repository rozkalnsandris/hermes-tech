from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import source_health


class SourceHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.path = Path(self.tmp_obj.name) / "data" / "source-health.json"

    def test_missing_state_starts_empty_and_round_trips_atomically(self) -> None:
        state = source_health.load_state(self.path)
        self.assertEqual(state, {"format_version": 1, "sources": {}})
        source_health.save_state(self.path, state)
        self.assertEqual(source_health.load_state(self.path), state)
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_success_records_transport_evidence_and_resets_failure_streak(self) -> None:
        state = source_health.empty_state()
        source_health.record_failure(
            state,
            "Example",
            at="2026-08-08T10:00:00+00:00",
            error_type="FeedTransportError",
        )
        source_health.record_success(
            state,
            "Example",
            at="2026-08-08T12:00:00+00:00",
            http_status=200,
            final_host="example.com",
            content_type="application/rss+xml",
            bytes_received=1234,
            redirects=1,
            feed_entries=8,
            new_articles=2,
        )
        entry = state["sources"]["Example"]
        self.assertEqual(entry["last_status"], "ok")
        self.assertEqual(entry["consecutive_failures"], 0)
        self.assertEqual(entry["last_http_status"], 200)
        self.assertEqual(entry["last_final_host"], "example.com")
        self.assertEqual(entry["last_new_articles"], 2)
        self.assertIsNone(entry["last_error_type"])

    def test_failure_streak_persists_without_storing_exception_message(self) -> None:
        state = source_health.empty_state()
        for hour in (10, 11, 12):
            source_health.record_failure(
                state,
                "Example",
                at=f"2026-08-08T{hour:02d}:00:00+00:00",
                error_type="FeedTransportError",
            )
        source_health.save_state(self.path, state)
        raw = self.path.read_text(encoding="utf-8")
        entry = json.loads(raw)["sources"]["Example"]
        self.assertEqual(entry["consecutive_failures"], 3)
        self.assertEqual(entry["last_error_type"], "FeedTransportError")
        self.assertNotIn("password", raw.lower())
        self.assertNotIn("exception_message", raw)

    def test_warning_thresholds_distinguish_failure_streak_and_staleness(self) -> None:
        state = source_health.empty_state()
        source_health.record_success(
            state,
            "Stale",
            at="2026-08-05T12:00:00+00:00",
            http_status=200,
            final_host="stale.example",
            content_type="application/rss+xml",
            bytes_received=100,
            redirects=0,
            feed_entries=0,
            new_articles=0,
        )
        for hour in (10, 11, 12):
            source_health.record_failure(
                state,
                "Broken",
                at=f"2026-08-08T{hour:02d}:00:00+00:00",
                error_type="RuntimeError",
            )
        warnings = source_health.health_warnings(
            state,
            ["Stale", "Broken", "New"],
            now=datetime(2026, 8, 8, 13, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(any("Stale: last successful fetch" in item for item in warnings))
        self.assertIn("Broken: 3 consecutive fetch failures", warnings)
        self.assertIn("Broken: no successful fetch recorded yet", warnings)
        self.assertIn("New: no health history yet", warnings)

    def test_malformed_or_future_state_fails_closed(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"format_version": 99, "sources": {}}', encoding="utf-8")
        with self.assertRaises(source_health.SourceHealthError):
            source_health.load_state(self.path)

        self.path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(source_health.SourceHealthError):
            source_health.load_state(self.path)


if __name__ == "__main__":
    unittest.main()
