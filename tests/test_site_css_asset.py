import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BASE_TEMPLATE = SITE / "layouts" / "_default" / "baseof.html"
CSS_SOURCE = SITE / "assets" / "css" / "site.css"

STYLESHEET_RE = re.compile(
    r'<link rel="stylesheet" '
    r'href="(?P<href>/css/site\.min\.[0-9a-f]+\.css)" '
    r'integrity="(?P<integrity>sha384-[A-Za-z0-9+/=]+)" '
    r'crossorigin="anonymous">'
)


class SiteCssAssetContractTests(unittest.TestCase):
    def test_base_template_uses_hugo_asset_pipeline(self) -> None:
        template = BASE_TEMPLATE.read_text(encoding="utf-8")

        self.assertNotIn("<style", template)
        self.assertIn(
            '{{ $style := resources.Get "css/site.css" | minify | fingerprint "sha384" }}',
            template,
        )
        self.assertIn(
            '<link rel="stylesheet" href="{{ $style.RelPermalink }}" '
            'integrity="{{ $style.Data.Integrity }}" crossorigin="anonymous">',
            template,
        )

    def test_css_source_has_one_canonical_blockquote_rule(self) -> None:
        css = CSS_SOURCE.read_text(encoding="utf-8")

        self.assertEqual(len(re.findall(r"\.prose blockquote\s*\{", css)), 1)
        self.assertNotRegex(css, r"HERMES_[A-Z0-9_]+_V\d+")
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", css)
        self.assertIn('/brand/hermes-tech-mark-v15.png', css)

    def test_representative_pages_share_one_fingerprinted_stylesheet(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermes-tech-css-") as temporary:
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
                ],
                cwd=ROOT,
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            pages = [
                destination / "index.html",
                destination / "digest" / "index.html",
                destination / "ai" / "index.html",
                destination / "agents" / "index.html",
                destination / "how-hermes-works" / "index.html",
                destination / "impressum" / "index.html",
            ]
            article_pages = sorted(
                page
                for section in ("digest", "ai", "agents")
                for page in (destination / section).glob("*/index.html")
            )
            self.assertTrue(article_pages, "Hugo build produced no representative article page")
            pages.append(article_pages[0])

            references: set[tuple[str, str]] = set()
            for page in pages:
                self.assertTrue(page.is_file(), f"missing representative page: {page}")
                html = page.read_text(encoding="utf-8")
                self.assertNotIn("<style", html)
                match = STYLESHEET_RE.search(html)
                self.assertIsNotNone(match, f"missing fingerprinted stylesheet in {page}")
                assert match is not None
                references.add((match.group("href"), match.group("integrity")))

            self.assertEqual(len(references), 1)
            href, _integrity = references.pop()
            generated_css = destination / href.lstrip("/")
            self.assertTrue(generated_css.is_file(), f"missing generated CSS asset: {generated_css}")
            self.assertGreater(generated_css.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
