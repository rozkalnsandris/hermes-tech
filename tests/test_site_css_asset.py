import os
import re
import subprocess
import tempfile
import unittest
from html import unescape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BASE_TEMPLATE = SITE / "layouts" / "baseof.html"
STYLES_PARTIAL = SITE / "layouts" / "_partials" / "head" / "styles.html"
CSS_SOURCE = SITE / "assets" / "css" / "site.css"

LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
ATTRIBUTE_RE = re.compile(r'([A-Za-z_:][-A-Za-z0-9_:.]*)="([^"]*)"')
FINGERPRINTED_CSS_RE = re.compile(r"^/css/site\.min\.[0-9a-f]+\.css$")
INTEGRITY_RE = re.compile(r"^sha384-[A-Za-z0-9+/=]+$")


def stylesheet_reference(html: str) -> tuple[str, str]:
    stylesheet_links = []
    for tag in LINK_RE.findall(html):
        attributes = {
            name: unescape(value)
            for name, value in ATTRIBUTE_RE.findall(tag)
        }
        if attributes.get("rel") == "stylesheet":
            stylesheet_links.append(attributes)

    if len(stylesheet_links) != 1:
        raise AssertionError(f"expected one stylesheet link, found {stylesheet_links!r}")

    attributes = stylesheet_links[0]
    href = attributes.get("href", "")
    integrity = attributes.get("integrity", "")
    if not FINGERPRINTED_CSS_RE.fullmatch(href):
        raise AssertionError(f"unexpected stylesheet href: {href!r}")
    if not INTEGRITY_RE.fullmatch(integrity):
        raise AssertionError(f"unexpected stylesheet integrity: {integrity!r}")
    if attributes.get("crossorigin") != "anonymous":
        raise AssertionError(f"missing anonymous CORS mode: {attributes!r}")
    return href, integrity


class SiteCssAssetContractTests(unittest.TestCase):
    def test_base_template_delegates_hugo_asset_pipeline_to_styles_partial(self) -> None:
        base = BASE_TEMPLATE.read_text(encoding="utf-8")
        styles = STYLES_PARTIAL.read_text(encoding="utf-8")

        self.assertIn('{{ partial "head/styles.html" . }}', base)
        self.assertNotIn("resources.Get", base)
        self.assertNotIn("<style", styles)
        self.assertIn(
            '{{ $style := resources.Get "css/site.css" | minify | fingerprint "sha384" }}',
            styles,
        )
        self.assertIn(
            '<link rel="stylesheet" href="{{ $style.RelPermalink }}" '
            'integrity="{{ $style.Data.Integrity }}" crossorigin="anonymous">',
            styles,
        )

    def test_css_source_has_one_canonical_blockquote_rule(self) -> None:
        css = CSS_SOURCE.read_text(encoding="utf-8")

        canonical_rules = re.findall(r"(?m)^\s*\.prose blockquote\s*\{", css)
        self.assertEqual(len(canonical_rules), 1)
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
                references.add(stylesheet_reference(html))

            self.assertEqual(len(references), 1)
            href, _integrity = references.pop()
            generated_css = destination / href.lstrip("/")
            self.assertTrue(generated_css.is_file(), f"missing generated CSS asset: {generated_css}")
            self.assertGreater(generated_css.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
