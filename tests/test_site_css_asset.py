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
CSS_DIR = SITE / "assets" / "css"
CSS_MODULE_NAMES = (
    "tokens.css",
    "base.css",
    "layout.css",
    "components.css",
    "home.css",
    "article.css",
    "responsive.css",
)

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


def css_modules() -> dict[str, str]:
    return {
        name: (CSS_DIR / name).read_text(encoding="utf-8")
        for name in CSS_MODULE_NAMES
    }


class SiteCssAssetContractTests(unittest.TestCase):
    def test_styles_partial_concatenates_modules_before_minify_and_fingerprint(self) -> None:
        base = BASE_TEMPLATE.read_text(encoding="utf-8")
        styles = STYLES_PARTIAL.read_text(encoding="utf-8")

        self.assertIn('{{ partial "head/styles.html" . }}', base)
        self.assertNotIn("resources.Get", base)
        self.assertNotIn("<style", styles)

        positions = []
        for name in CSS_MODULE_NAMES:
            marker = f'resources.Get "css/{name}"'
            self.assertEqual(styles.count(marker), 1, marker)
            positions.append(styles.index(marker))
        self.assertEqual(positions, sorted(positions))

        self.assertIn('| resources.Concat "css/site.css" | minify | fingerprint "sha384"', styles)
        self.assertIn(
            '<link rel="stylesheet" href="{{ $style.RelPermalink }}" '
            'integrity="{{ $style.Data.Integrity }}" crossorigin="anonymous">',
            styles,
        )

    def test_css_modules_have_one_authoritative_visual_system(self) -> None:
        modules = css_modules()
        combined = "\n".join(modules[name] for name in CSS_MODULE_NAMES)

        self.assertFalse((CSS_DIR / "site.css").exists())
        self.assertEqual(len(re.findall(r"(?m)^\s*:root\s*\{", combined)), 1)
        self.assertIn("--bg0:#03101f", modules["tokens.css"])
        self.assertIn("--devops:#22c7ff", modules["tokens.css"])
        self.assertIn("--ai:#91a7ff", modules["tokens.css"])
        self.assertNotIn("#0c0e12", combined)
        self.assertNotIn("/* Brand identity */", combined)
        self.assertNotIn("/hermes-winged-h-v9.svg", combined)
        self.assertNotIn("@keyframes blink", combined)
        self.assertNotIn(".brand .cur", combined)

        self.assertIn("background:rgba(3,16,31,.90)", modules["layout.css"])
        self.assertIn("width:220px;height:auto;max-height:42px", modules["layout.css"])
        self.assertIn('/brand/hermes-tech-mark-v15.png', modules["layout.css"])
        self.assertIn('/brand/hermes-tech-mark-v15.png', modules["home.css"])
        self.assertIn('/brand/hermes-tech-mark-v15.png', modules["article.css"])
        self.assertEqual(len(re.findall(r"(?m)^\s*\.prose blockquote\s*\{", combined)), 1)

        important_uses = re.findall(r"[^;{}]+!important", combined)
        self.assertEqual(len(important_uses), 2)
        self.assertTrue(all("none!important" in value for value in important_uses))
        for name in CSS_MODULE_NAMES[:-1]:
            self.assertNotIn("!important", modules[name], name)

    def test_responsive_accessibility_and_section_accents_are_preserved(self) -> None:
        modules = css_modules()
        responsive = modules["responsive.css"]
        components = modules["components.css"]
        article = modules["article.css"]
        home = modules["home.css"]

        self.assertIn("@media (prefers-reduced-motion: reduce)", responsive)
        self.assertIn("animation:none!important;transition:none!important", responsive)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", responsive)
        self.assertIn(".hmark{width:166px;max-height:33px}", responsive)
        self.assertIn(".tag-digest{color:var(--devops)", components)
        self.assertIn(".tag-ai{color:var(--ai)", components)
        self.assertIn(".tag-agents{color:var(--agents)", components)
        self.assertIn("body.sec-digest .prose a{color:var(--devops)}", article)
        self.assertIn("body.sec-ai .prose a{color:var(--ai)}", article)
        self.assertIn("body.sec-agents .prose a{color:var(--agents)}", article)
        self.assertIn(".log.c-digest:hover{border-left-color:var(--devops)}", home)
        self.assertIn(".log.c-ai:hover{border-left-color:var(--ai)}", home)
        self.assertIn(".log.c-agents:hover{border-left-color:var(--agents)}", home)

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
                    "--panicOnWarning",
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
