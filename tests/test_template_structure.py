from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
LAYOUTS = SITE / "layouts"
BASE = LAYOUTS / "baseof.html"
HOME = LAYOUTS / "home.html"
LIST = LAYOUTS / "list.html"
SINGLE = LAYOUTS / "single.html"
PARTIALS = LAYOUTS / "_partials"


class TemplateStructureTests(unittest.TestCase):
    def test_hugo_0164_canonical_template_paths_are_used(self) -> None:
        for path in (BASE, HOME, LIST, SINGLE):
            self.assertTrue(path.is_file(), path)

        legacy = (
            LAYOUTS / "index.html",
            LAYOUTS / "_default" / "baseof.html",
            LAYOUTS / "_default" / "list.html",
            LAYOUTS / "_default" / "single.html",
            LAYOUTS / "partials",
        )
        for path in legacy:
            self.assertFalse(path.exists(), path)

    def test_base_is_skeleton_and_composes_bounded_partials(self) -> None:
        text = BASE.read_text(encoding="utf-8")
        for call in (
            '{{ partial "head/metadata.html" . }}',
            '{{ partial "head/styles.html" . }}',
            '{{ partial "header.html" . }}',
            '{{ partial "footer.html" . }}',
        ):
            self.assertIn(call, text)

        self.assertIn('{{ block "main" . }}{{ end }}', text)
        self.assertIn('<main class="wrap">', text)
        self.assertNotIn("resources.Get", text)
        self.assertNotIn('<nav class="site-nav"', text)
        self.assertNotIn("without per-run human approval", text)
        self.assertNotIn('property="og:', text)

    def test_home_and_list_share_digest_row_partial(self) -> None:
        for path in (HOME, LIST):
            text = path.read_text(encoding="utf-8")
            self.assertGreaterEqual(text.count('{{ partial "digest-row.html" . }}'), 1, path)
            self.assertNotIn('<a class="log c-', text, path)

        list_text = LIST.read_text(encoding="utf-8")
        self.assertIn('partial "section-paginator.html" .', list_text)

        paginator = (PARTIALS / "section-paginator.html").read_text(encoding="utf-8")
        self.assertEqual(paginator.count(".Paginate"), 1)
        self.assertIn(".Pages.ByDate.Reverse", paginator)

        row = (PARTIALS / "digest-row.html").read_text(encoding="utf-8")
        self.assertIn('<a class="log c-{{ .Section }}"', row)
        self.assertIn('{{ .Date.Format "2006-01-02" }}', row)
        self.assertIn('{{ delimit . " · " }}', row)

    def test_rendered_landmarks_metadata_and_navigation_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermes-tech-templates-") as temporary:
            build_root = Path(temporary)
            destination = build_root / "public"
            cache = build_root / "cache"
            destination.mkdir()
            cache.mkdir()

            environment = os.environ.copy()
            environment["HUGO_CACHEDIR"] = str(cache)
            subprocess.run(
                [
                    "hugo",
                    "--source",
                    str(SITE),
                    "--destination",
                    str(destination),
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

            home = (destination / "index.html").read_text(encoding="utf-8")
            digest = (destination / "digest" / "index.html").read_text(encoding="utf-8")
            how = (destination / "how-hermes-works" / "index.html").read_text(encoding="utf-8")
            article_pages = sorted((destination / "digest").glob("*/index.html"))
            self.assertTrue(article_pages, "Hugo build produced no digest article")
            article = article_pages[0].read_text(encoding="utf-8")

            for html in (home, digest, how, article):
                self.assertEqual(html.count('<header class="site">'), 1)
                self.assertEqual(html.count('<main class="wrap">'), 1)
                self.assertEqual(html.count('<footer class="site">'), 1)
                self.assertRegex(html, r'<link rel="canonical" href="https://tech\.rozkalns\.net/[^\"]*">')
                self.assertIn('<nav class="site-nav" aria-label="Primary navigation">', html)

            self.assertIn('<a href="/" aria-current="page">home</a>', home)
            self.assertIn(
                '<a href="/digest/" class="n-devops" aria-current="page">devops</a>',
                digest,
            )
            self.assertIn(
                '<a href="/how-hermes-works/" aria-current="page">how it works</a>',
                how,
            )
            self.assertRegex(home, r'<a class="log c-(?:digest|ai|agents)" href="https://tech\.rozkalns\.net/')
            self.assertRegex(digest, r'<a class="log c-digest" href="https://tech\.rozkalns\.net/digest/')


if __name__ == "__main__":
    unittest.main()
