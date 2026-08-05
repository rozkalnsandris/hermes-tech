from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hermes_time


class RssTimestampTests(unittest.TestCase):
    def test_feedparser_utc_tuple_is_host_timezone_independent(self) -> None:
        parsed = time.struct_time((2026, 7, 1, 12, 34, 56, 2, 182, 0))
        expected = "2026-07-01T12:34:56+00:00"
        original = os.environ.get("TZ")
        self.addCleanup(self._restore_tz, original)

        zones = (
            "UTC0",
            "CET-1",
            "CET-1CEST,M3.5.0/2,M10.5.0/3",
        )
        for zone in zones:
            with self.subTest(zone=zone):
                os.environ["TZ"] = zone
                time.tzset()
                self.assertEqual(
                    hermes_time.rss_struct_time_to_utc_iso(parsed),
                    expected,
                )
                entry = SimpleNamespace(
                    published_parsed=parsed,
                    updated_parsed=None,
                )
                self.assertEqual(
                    hermes_time.collector_entry_published(entry),
                    expected,
                )

    @staticmethod
    def _restore_tz(original: str | None) -> None:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()

    def test_updated_is_fallback_and_missing_timestamp_is_empty(self) -> None:
        updated = time.struct_time((2026, 1, 15, 8, 0, 0, 3, 15, 0))
        self.assertEqual(
            hermes_time.collector_entry_published(
                SimpleNamespace(published_parsed=None, updated_parsed=updated)
            ),
            "2026-01-15T08:00:00+00:00",
        )
        self.assertEqual(
            hermes_time.collector_entry_published(SimpleNamespace()),
            "",
        )


class BusinessTimezoneTests(unittest.TestCase):
    def test_local_after_midnight_uses_new_berlin_date(self) -> None:
        instant = datetime(2026, 8, 5, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(hermes_time.business_date(instant), "2026-08-06")

    def test_winter_and_summer_publication_offsets(self) -> None:
        self.assertEqual(
            hermes_time.publication_timestamp("2026-01-15"),
            "2026-01-15T07:00:00+01:00",
        )
        self.assertEqual(
            hermes_time.publication_timestamp("2026-07-15"),
            "2026-07-15T07:00:00+02:00",
        )

    def test_dst_transition_dates_use_correct_offset(self) -> None:
        expected = {
            "2026-03-28": "+01:00",
            "2026-03-29": "+02:00",
            "2026-10-24": "+02:00",
            "2026-10-25": "+01:00",
        }
        for date_text, offset in expected.items():
            with self.subTest(date=date_text):
                self.assertTrue(
                    hermes_time.publication_timestamp(date_text).endswith(offset)
                )

    def test_digest_datetime_changes_only_business_date_format(self) -> None:
        instant = hermes_time.DigestDateTime(
            2026, 8, 5, 22, 30, tzinfo=timezone.utc
        )
        self.assertEqual(instant.strftime("%Y-%m-%d"), "2026-08-06")
        self.assertEqual(
            instant.isoformat(timespec="seconds"),
            "2026-08-05T22:30:00+00:00",
        )
        self.assertEqual(instant.strftime("%H:%M"), "22:30")

    def test_cli_is_deterministic(self) -> None:
        env = dict(os.environ)
        env["TZ"] = "UTC0"
        business = subprocess.run(
            [
                sys.executable,
                str(ROOT / "hermes_time.py"),
                "business-date",
                "--at",
                "2026-08-05T22:30:00Z",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(business.stdout.strip(), "2026-08-06")
        publication = subprocess.run(
            [
                sys.executable,
                str(ROOT / "hermes_time.py"),
                "publication-timestamp",
                "2026-10-25",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(
            publication.stdout.strip(),
            "2026-10-25T07:00:00+01:00",
        )


class InstallerTests(unittest.TestCase):
    def test_collector_and_digest_installers_are_idempotent(self) -> None:
        collector = SimpleNamespace(entry_published=lambda _entry: "old")
        hermes_time.install_collector_time_contracts(collector)
        installed = collector.entry_published
        hermes_time.install_collector_time_contracts(collector)
        self.assertIs(collector.entry_published, installed)
        self.assertEqual(
            collector.entry_published(
                SimpleNamespace(
                    published_parsed=time.struct_time(
                        (2026, 1, 1, 0, 0, 0, 3, 1, 0)
                    )
                )
            ),
            "2026-01-01T00:00:00+00:00",
        )

        core = SimpleNamespace(datetime=datetime)
        hermes_time.install_digest_time_contracts(core)
        self.assertIs(core.datetime, hermes_time.DigestDateTime)
        hermes_time.install_digest_time_contracts(core)
        self.assertIs(core.datetime, hermes_time.DigestDateTime)

    def test_digest_now_provider_preserves_utc_timestamp_but_uses_berlin_date(self) -> None:
        fixed = datetime(2026, 8, 5, 22, 30, tzinfo=timezone.utc)
        with patch.object(hermes_time, "_UTC_NOW_PROVIDER", return_value=fixed):
            current = hermes_time.DigestDateTime.now(timezone.utc)
        self.assertEqual(current.strftime("%Y-%m-%d"), "2026-08-06")
        self.assertEqual(
            current.isoformat(timespec="seconds"),
            "2026-08-05T22:30:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
