from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
POLICY_PATH = ROOT / "docs" / "http-policy.json"
FINGERPRINTED_CSS_RE = re.compile(r"^/css/site\.min\.[0-9a-f]+\.css$")


def local_reference(value: str) -> bool:
    parsed = urlsplit(value)
    return not parsed.scheme and not parsed.netloc and value.startswith("/")


class HttpPolicyContractTests(unittest.TestCase):
    def test_machine_readable_policy_is_strict_and_explicit(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(
            policy["cache_control"]["fingerprinted_css"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(policy["cache_control"]["html"], "no-cache")
        self.assertEqual(policy["cache_control"]["stable_metadata"], "no-cache")

        headers = policy["security_headers"]
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(
            headers["Permissions-Policy"],
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        csp = headers["Content-Security-Policy"]
        for directive in (
            "default-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
            "img-src 'self'",
            "style-src 'self'",
            "script-src 'none'",
            "object-src 'none'",
            "manifest-src 'self'",
        ):
            self.assertIn(directive, csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertNotIn("'unsafe-eval'", csp)

    def test_generated_site_matches_strict_csp_assumptions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hermes-tech-http-policy-") as temporary:
            build_root = Path(temporary)
            public = build_root / "public"
            cache = build_root / "cache"
            public.mkdir()
            cache.mkdir()
            environment = os.environ.copy()
            environment["HUGO_CACHEDIR"] = str(cache)
            subprocess.run(
                [
                    "hugo",
                    "--source",
                    str(SITE),
                    "--destination",
                    str(public),
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

            html_files = sorted(public.rglob("*.html"))
            self.assertTrue(html_files)
            stylesheet_hrefs: set[str] = set()
            for path in html_files:
                html = path.read_text(encoding="utf-8")
                lowered = html.casefold()
                for forbidden in ("<script", "<style", " style=", "<iframe", "<object", "<embed", "<form"):
                    self.assertNotIn(forbidden, lowered, path)

                for href in re.findall(r'<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"', html, re.I):
                    self.assertTrue(local_reference(href), (path, href))
                    stylesheet_hrefs.add(href)
                for src in re.findall(r'<img\b[^>]*src="([^"]+)"', html, re.I):
                    self.assertTrue(local_reference(src), (path, src))

            self.assertEqual(len(stylesheet_hrefs), 1)
            css_href = stylesheet_hrefs.pop()
            self.assertRegex(css_href, FINGERPRINTED_CSS_RE)
            css = (public / css_href.lstrip("/")).read_text(encoding="utf-8")
            self.assertNotRegex(css, r"(?i)@import\s")
            for target in re.findall(r"url\((?:['\"])?([^)'\"]+)", css, re.I):
                self.assertTrue(local_reference(target), target)


if __name__ == "__main__":
    unittest.main()
