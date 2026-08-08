#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO / "tools" / "capture_site_baseline.py"

spec = importlib.util.spec_from_file_location("capture_site_baseline", TOOL_PATH)
baseline_tool = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(baseline_tool)


class CaptureSiteBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.public = Path(self.tmp_obj.name) / "public"
        (self.public / "css").mkdir(parents=True)
        (self.public / "posts" / "example").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp_obj.cleanup()

    def _write(self, relative: str, content: str) -> Path:
        path = self.public / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_capture_is_deterministic_and_records_asset_contract(self) -> None:
        css = self._write(
            "css/site.min.abcdef.css",
            "body{margin:0}\n",
        )
        self._write(
            "index.html",
            (
                '<!doctype html><html><head>'
                '<link rel="stylesheet" href="/css/site.min.abcdef.css" '
                'integrity="sha384-test" crossorigin="anonymous">'
                "</head><body>home</body></html>\n"
            ),
        )
        self._write(
            "posts/example/index.html",
            "<!doctype html><html><body>article</body></html>\n",
        )
        self._write("index.xml", "<rss></rss>\n")

        first = baseline_tool.capture_baseline(
            self.public,
            source_revision="deadbeef",
            hugo_version="hugo v0.test+extended",
        )
        second = baseline_tool.capture_baseline(
            self.public,
            source_revision="deadbeef",
            hugo_version="hugo v0.test+extended",
        )

        self.assertEqual(
            baseline_tool.render_baseline(first),
            baseline_tool.render_baseline(second),
        )
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["source_revision"], "deadbeef")
        self.assertEqual(first["generated"]["files"], 4)
        self.assertEqual(first["generated"]["html"]["files"], 2)
        self.assertEqual(first["generated"]["css"]["files"], 1)
        self.assertEqual(first["generated"]["xml"]["files"], 1)
        self.assertEqual(first["generated"]["script_tags"], 0)

        paths = [item["path"] for item in first["manifest"]]
        self.assertEqual(paths, sorted(paths))

        css_entry = next(
            item for item in first["manifest"]
            if item["path"] == "css/site.min.abcdef.css"
        )
        self.assertEqual(css_entry["bytes"], css.stat().st_size)
        self.assertEqual(
            css_entry["sha256"],
            hashlib.sha256(css.read_bytes()).hexdigest(),
        )

        self.assertEqual(
            first["homepage_stylesheets"],
            [
                {
                    "href": "/css/site.min.abcdef.css",
                    "integrity": "sha384-test",
                    "crossorigin": "anonymous",
                    "path": "css/site.min.abcdef.css",
                    "bytes": css.stat().st_size,
                    "sha256": hashlib.sha256(css.read_bytes()).hexdigest(),
                }
            ],
        )

    def test_script_tags_are_counted_across_generated_html(self) -> None:
        self._write(
            "index.html",
            (
                "<html><body>"
                '<script src="/app.js"></script>'
                '<script type="application/ld+json">{}</script>'
                "</body></html>\n"
            ),
        )
        self._write(
            "posts/example/index.html",
            "<html><body><script>console.log('x')</script></body></html>\n",
        )

        result = baseline_tool.capture_baseline(self.public)
        self.assertEqual(result["generated"]["script_tags"], 3)

    def test_external_or_escaping_stylesheet_is_not_resolved_locally(self) -> None:
        self._write(
            "index.html",
            (
                '<link rel="stylesheet" href="https://example.com/site.css" '
                'integrity="sha384-external">'
                '<link rel="stylesheet" href="../outside.css">'
            ),
        )

        result = baseline_tool.capture_baseline(self.public)
        self.assertEqual(
            result["homepage_stylesheets"],
            [
                {
                    "href": "https://example.com/site.css",
                    "integrity": "sha384-external",
                    "crossorigin": None,
                },
                {
                    "href": "../outside.css",
                    "integrity": None,
                    "crossorigin": None,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
