from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import format_digest
import format_digest_core


REPO = Path(__file__).resolve().parents[1]


class HermesSeparationTests(unittest.TestCase):
    def test_inline_marker_moves_to_own_blockquote(self) -> None:
        source = (
            "### Release\n\n"
            "Operational summary. 💬 Hermes: Concrete analysis follows.\n"
        )
        rendered = format_digest.separate_hermes(source)
        self.assertIn("Operational summary.\n\n> **Hermes:** Concrete analysis follows.", rendered)
        self.assertNotIn("summary. 💬 Hermes", rendered)

    def test_existing_blockquote_is_normalised_without_empty_emphasis(self) -> None:
        source = "> 💬 **Hermes:** Existing analysis.\n"
        rendered = format_digest.separate_hermes(source)
        self.assertEqual(rendered, "> **Hermes:** Existing analysis.\n")
        self.assertNotIn("** **", rendered)

    def test_marker_inside_fenced_code_is_not_rewritten(self) -> None:
        source = "```text\nHermes: literal fixture\n```\n"
        self.assertEqual(format_digest.separate_hermes(source), source)

    def test_crlf_and_excess_blank_lines_are_canonicalised(self) -> None:
        source = "Title\r\n\r\n\r\nHermes: Analysis.\r\n"
        rendered = format_digest.separate_hermes(source)
        self.assertEqual(rendered, "Title\n\n> **Hermes:** Analysis.\n")


class CoreFormattingTests(unittest.TestCase):
    def test_compact_source_format_becomes_canonical_source_line(self) -> None:
        block = (
            "**New runtime** Operational details.\n"
            "Source: [Vendor announcement](https://example.test/runtime)\n"
            "Hermes: Practical analysis."
        )
        rendered = format_digest_core.process_block(block)
        self.assertIn("### New runtime", rendered)
        self.assertIn(
            "Source: [Vendor announcement](https://example.test/runtime)",
            rendered,
        )
        self.assertIn("> 💬 **Hermes:** Practical analysis.", rendered)

    def test_legacy_source_label_is_replaced_with_heading(self) -> None:
        block = (
            "**Database release** Transaction behavior changed.\n"
            "[Source](https://example.test/database)"
        )
        rendered = format_digest_core.process_block(block)
        self.assertIn(
            "Source: [Database release](https://example.test/database)",
            rendered,
        )

    def test_blockquote_is_preserved_and_empty_emphasis_removed(self) -> None:
        block = "> **Hermes:** ** **The trade-off is operational complexity."
        rendered = format_digest_core.process_block(block)
        self.assertEqual(
            rendered,
            "> **Hermes:** The trade-off is operational complexity.",
        )
        self.assertNotIn("** **", rendered)

    def test_non_article_intro_is_left_readable(self) -> None:
        intro = "A compact introduction without an article heading."
        self.assertEqual(format_digest_core.process_block(intro), intro)


class FormatterIntegrationTests(unittest.TestCase):
    def test_full_formatter_pipeline_keeps_source_and_hermes_separate(self) -> None:
        source = (
            "**Runtime update** Operational summary.\n"
            "Source: [Runtime project](https://example.test/runtime)\n"
            "💬 Hermes: The change matters because rollout behavior is different.\n"
        )
        proc = subprocess.run(
            [sys.executable, str(REPO / "format_digest.py")],
            input=source,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=REPO,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("### Runtime update", proc.stdout)
        self.assertIn(
            "Source: [Runtime project](https://example.test/runtime)",
            proc.stdout,
        )
        self.assertIn(
            "> **Hermes:** The change matters because rollout behavior is different.",
            proc.stdout,
        )
        self.assertNotIn("** **", proc.stdout)


if __name__ == "__main__":
    unittest.main()
