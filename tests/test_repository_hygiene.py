#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO / "tools" / "check_repository_hygiene.py"

spec = importlib.util.spec_from_file_location("repository_hygiene", TOOL_PATH)
hygiene = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(hygiene)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


class RepositoryHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_obj.name) / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "--initial-branch=main")
        git(self.root, "config", "user.name", "Hermes Test")
        git(self.root, "config", "user.email", "hermes-test@example.invalid")
        self._write_clean_fixture()
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")

    def tearDown(self) -> None:
        self.tmp_obj.cleanup()

    def _write(self, relative: str, content: str = "") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_bytes(self, relative: str, content: bytes = b"asset") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _write_clean_fixture(self) -> None:
        self._write(
            ".gitignore",
            "\n".join(
                [
                    "/.local-backups/",
                    "/.publish-work.*",
                    "/site/.hugo_build.lock",
                    "/site/resources/",
                    "/.pytest_cache/",
                    "/.mypy_cache/",
                    "/.ruff_cache/",
                    "/evidence/",
                    "/*.evidence.json",
                    "/*-evidence-*.tar.gz",
                    "/*.bundle",
                    "/*.bundle.*",
                    "",
                ]
            ),
        )
        for relative in hygiene.CANONICAL_EDITORIAL_FILES:
            self._write(relative, f"# {Path(relative).stem}\n")
        self._write(
            "digest_core.py",
            textwrap.dedent(
                """
                from pathlib import Path
                BASE = Path(".")
                def load_editorial_context():
                    editorial_dir = BASE / "editorial"
                    parts = []
                    for name in ("VOICE.md", "WRITING.md", "REVIEW.md"):
                        path = editorial_dir / name
                        parts.append(path.read_text())
                    return "\\n".join(parts)
                """
            ).lstrip(),
        )
        for relative in hygiene.ROOT_PERSONA_FILES:
            self._write(
                relative,
                f"> {hygiene.HISTORICAL_PERSONA_MARKER}\n\n# Historical\n",
            )
        self._write(
            "AGENTS.md",
            "\n".join(
                [
                    hygiene.AGENTS_NONCANONICAL_MARKER,
                    *(
                        f"`{relative}`"
                        for relative in hygiene.CANONICAL_EDITORIAL_FILES
                    ),
                    "",
                ]
            ),
        )
        self._write(
            "site/layouts/_default/baseof.html",
            textwrap.dedent(
                """
                <link rel="icon" href="/favicon-v15.ico">
                <link rel="manifest" href="/site-v15.webmanifest">
                <meta property="og:image"
                      content="{{ "brand/hermes-tech-og-v15.jpg" | absURL }}">
                <style>
                .mark{background:url("/brand/hermes-tech-mark-v15.png")}
                </style>
                """
            ).lstrip(),
        )
        self._write(
            "site/static/site-v15.webmanifest",
            '{"icons":[{"src":"/android-chrome-192x192-v15.png"}]}\n',
        )
        for relative in (
            "site/static/favicon-v15.ico",
            "site/static/brand/hermes-tech-og-v15.jpg",
            "site/static/brand/hermes-tech-mark-v15.png",
            "site/static/android-chrome-192x192-v15.png",
        ):
            self._write_bytes(relative)

    def _validate_error(self) -> str:
        with self.assertRaises(hygiene.HygieneError) as raised:
            hygiene.validate_repository(self.root)
        return str(raised.exception)

    def test_clean_fixture_passes_and_reports_retained_assets(self) -> None:
        report = hygiene.validate_repository(self.root)
        self.assertGreaterEqual(len(report["referenced_static_assets"]), 4)
        self.assertEqual(report["unreferenced_versioned_assets_retained"], ())

    def test_tracked_hugo_lock_and_layout_backup_fail(self) -> None:
        self._write("site/.hugo_build.lock")
        self._write("site/layouts.bak-20260716/index.html", "old")
        git(self.root, "add", "-f", "site/.hugo_build.lock",
            "site/layouts.bak-20260716/index.html")
        message = self._validate_error()
        self.assertIn("tracked transient", message)
        self.assertIn("site/.hugo_build.lock", message)

    def test_active_publication_path_must_not_be_ignored(self) -> None:
        with (self.root / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("/digests/\n")
        message = self._validate_error()
        self.assertIn("intentional tracked publication", message)
        self.assertIn("digests/2099-01-01-devops.md", message)

    def test_digest_loader_must_match_canonical_editorial_order(self) -> None:
        self._write(
            "digest_core.py",
            textwrap.dedent(
                """
                from pathlib import Path
                BASE = Path(".")
                def load_editorial_context():
                    editorial_dir = BASE / "editorial"
                    return (editorial_dir / "STYLE.md").read_text()
                """
            ).lstrip(),
        )
        message = self._validate_error()
        self.assertIn("digest editorial loader drift", message)

    def test_missing_referenced_static_asset_fails(self) -> None:
        (self.root / "site/static/favicon-v15.ico").unlink()
        git(self.root, "rm", "-q", "site/static/favicon-v15.ico")
        message = self._validate_error()
        self.assertIn("reference missing static assets", message)
        self.assertIn("site/static/favicon-v15.ico", message)


if __name__ == "__main__":
    unittest.main()
