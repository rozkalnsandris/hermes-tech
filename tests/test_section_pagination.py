from __future__ import annotations

import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
SECTIONS = ("digest", "ai", "agents")
PAGER_SIZE = 10
BASE_URL = "https://tech.rozkalns.net"


def source_dates(section: str) -> list[str]:
    content_dir = SITE / "content" / section
    dates = [
        path.stem
        for path in content_dir.glob("*.md")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
    ]
    return sorted(dates, reverse=True)


def digest_row_dates(html: str, section: str) -> list[str]:
    pattern = re.compile(
        rf'<a class="log c-{re.escape(section)}"[^>]*>.*?'
        r'<span class="ts">(\d{4}-\d{2}-\d{2})</span>',
        re.DOTALL,
    )
    return pattern.findall(html)


def canonical_href(html: str) -> str:
    match = re.search(r'<link rel="canonical" href="([^"]+)">', html)
    if not match:
        raise AssertionError("missing canonical link")
    return match.group(1)


def rss_links(path: Path) -> list[str]:
    root = ET.parse(path).getroot()
    channel = root.find("channel")
    if channel is None:
        raise AssertionError(f"missing RSS channel: {path}")
    result = []
    for item in channel.findall("item"):
        link = item.findtext("link", default="")
        result.append(link)
    return result


class SectionPaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_obj = tempfile.TemporaryDirectory(prefix="hermes-tech-pagination-")
        build_root = Path(cls.tmp_obj.name)
        cls.public = build_root / "public"
        cache = build_root / "cache"
        cls.public.mkdir()
        cache.mkdir()

        environment = os.environ.copy()
        environment["HUGO_CACHEDIR"] = str(cache)
        subprocess.run(
            [
                "hugo",
                "--source",
                str(SITE),
                "--destination",
                str(cls.public),
                "--cleanDestinationDir",
                "--noBuildLock",
                "--panicOnWarning",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp_obj.cleanup()

    def pager_html(self, section: str, number: int) -> str:
        if number == 1:
            path = self.public / section / "index.html"
        else:
            path = self.public / section / "page" / str(number) / "index.html"
        self.assertTrue(path.is_file(), path)
        return path.read_text(encoding="utf-8")

    def test_sections_are_bounded_complete_and_newest_first(self) -> None:
        for section in SECTIONS:
            expected = source_dates(section)
            self.assertGreater(len(expected), 20, section)
            total_pages = math.ceil(len(expected) / PAGER_SIZE)
            self.assertGreaterEqual(total_pages, 3, section)

            observed: list[str] = []
            for number in range(1, total_pages + 1):
                page_dates = digest_row_dates(self.pager_html(section, number), section)
                self.assertLessEqual(len(page_dates), PAGER_SIZE, (section, number))
                self.assertEqual(
                    page_dates,
                    expected[(number - 1) * PAGER_SIZE : number * PAGER_SIZE],
                    (section, number),
                )
                observed.extend(page_dates)

            self.assertEqual(observed, expected, section)
            self.assertEqual(len(observed), len(set(observed)), section)
            self.assertFalse(
                (self.public / section / "page" / "1" / "index.html").exists(),
                section,
            )

    def test_first_middle_and_last_navigation(self) -> None:
        for section in SECTIONS:
            total_pages = math.ceil(len(source_dates(section)) / PAGER_SIZE)
            first = self.pager_html(section, 1)
            middle = self.pager_html(section, 2)
            last = self.pager_html(section, total_pages)

            for number, html in ((1, first), (2, middle), (total_pages, last)):
                self.assertIn('aria-label="Archive pages"', html, (section, number))
                self.assertIn(f"Page {number} of {total_pages}", html, (section, number))

            self.assertNotIn('rel="prev"', first, section)
            self.assertIn(
                f'rel="next" href="/{section}/page/2/"',
                first,
                section,
            )
            self.assertIn(
                f'rel="prev" href="/{section}/"',
                middle,
                section,
            )
            self.assertIn(
                f'rel="next" href="/{section}/page/3/"',
                middle,
                section,
            )
            self.assertIn('rel="prev"', last, section)
            self.assertNotIn('rel="next"', last, section)

    def test_paginated_pages_are_self_canonical(self) -> None:
        for section in SECTIONS:
            total_pages = math.ceil(len(source_dates(section)) / PAGER_SIZE)
            for number in range(1, total_pages + 1):
                expected = (
                    f"{BASE_URL}/{section}/"
                    if number == 1
                    else f"{BASE_URL}/{section}/page/{number}/"
                )
                self.assertEqual(
                    canonical_href(self.pager_html(section, number)),
                    expected,
                    (section, number),
                )

    def test_section_rss_remains_complete_and_unpaginated(self) -> None:
        for section in SECTIONS:
            expected = [f"{BASE_URL}/{section}/{date}/" for date in source_dates(section)]
            actual = rss_links(self.public / section / "index.xml")
            self.assertEqual(actual, expected, section)
            self.assertFalse((self.public / section / "page" / "2" / "index.xml").exists())

    def test_homepage_latest_feed_remains_independently_bounded(self) -> None:
        home = (self.public / "index.html").read_text(encoding="utf-8")
        rows = re.findall(r'<a class="log c-(?:digest|ai|agents)"', home)
        self.assertEqual(len(rows), 15)


if __name__ == "__main__":
    unittest.main()
