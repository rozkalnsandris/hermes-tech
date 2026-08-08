from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ROBOTS_TEMPLATE = SITE / "layouts" / "robots.txt"
DOC = ROOT / "docs" / "search-discovery.md"
ORIGIN = "https://tech.rozkalns.net/"
SITEMAP_URL = ORIGIN + "sitemap.xml"


class SearchDiscoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="hermes-tech-search-discovery-")
        build_root = Path(cls._tmp.name)
        cls.destination = build_root / "public"
        cache = build_root / "cache"
        cls.destination.mkdir()
        cache.mkdir()
        environment = os.environ.copy()
        environment["HUGO_CACHEDIR"] = str(cache)
        subprocess.run(
            [
                "hugo",
                "--source",
                str(SITE),
                "--destination",
                str(cls.destination),
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
        cls._tmp.cleanup()

    def test_source_robots_policy_is_explicit_and_canonical(self) -> None:
        self.assertEqual(
            ROBOTS_TEMPLATE.read_text(encoding="utf-8"),
            "User-agent: *\nAllow: /\n\nSitemap: https://tech.rozkalns.net/sitemap.xml\n",
        )

    def test_rendered_robots_advertises_generated_sitemap(self) -> None:
        robots = (self.destination / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("User-agent: *", robots)
        self.assertIn("Allow: /", robots)
        self.assertIn(f"Sitemap: {SITEMAP_URL}", robots)
        self.assertNotIn("Disallow: /", robots)
        self.assertTrue((self.destination / "sitemap.xml").is_file())

    def test_sitemap_is_valid_xml_and_stays_on_https_canonical_origin(self) -> None:
        sitemap = self.destination / "sitemap.xml"
        root = ET.parse(sitemap).getroot()
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        urls = [
            node.text.strip()
            for node in root.findall("sm:url/sm:loc", namespace)
            if node.text and node.text.strip()
        ]
        self.assertTrue(urls, "generated sitemap contains no URL entries")
        self.assertEqual(len(urls), len(set(urls)), "sitemap contains duplicate URLs")
        for url in urls:
            self.assertTrue(url.startswith(ORIGIN), url)
            self.assertNotIn("http://", url)
            self.assertNotIn("localhost", url.casefold())

        for required in (ORIGIN, ORIGIN + "digest/", ORIGIN + "ai/", ORIGIN + "agents/"):
            self.assertIn(required, urls)

    def test_representative_pages_have_https_canonical_and_no_noindex(self) -> None:
        pages = [
            self.destination / "index.html",
            self.destination / "digest" / "index.html",
            self.destination / "ai" / "index.html",
            self.destination / "agents" / "index.html",
        ]
        article_pages = sorted((self.destination / "digest").glob("*/index.html"))
        self.assertTrue(article_pages, "Hugo build produced no representative digest article")
        pages.append(article_pages[0])

        canonical_re = re.compile(r'<link rel="canonical" href="([^"]+)">')
        noindex_re = re.compile(
            r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*noindex',
            re.IGNORECASE,
        )
        for path in pages:
            html = path.read_text(encoding="utf-8")
            match = canonical_re.search(html)
            self.assertIsNotNone(match, path)
            canonical = match.group(1)
            self.assertTrue(canonical.startswith(ORIGIN), (path, canonical))
            self.assertNotRegex(html, noindex_re, path)

    def test_document_keeps_search_console_as_external_evidence(self) -> None:
        text = " ".join(DOC.read_text(encoding="utf-8").split())
        for marker in (
            "does **not** prove that Google or another search engine has crawled or indexed a URL",
            "Google Search Console is external account state",
            "Sitemap submission is a discovery hint, not an indexing guarantee",
            "HERMES_TECH_DEPLOY_REQUIRED=no",
            "RPI5_MAIN_CHANGE_REQUIRED=no",
        ):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
