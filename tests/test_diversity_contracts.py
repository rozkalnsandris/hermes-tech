from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from digest_diversity import diversity_filter, install_diversity_contracts


ROOT = Path(__file__).resolve().parents[1]


def article(
    article_id: int,
    topic_key: str,
    vendor: str,
    *,
    content_length: int = 10,
) -> dict:
    return {
        "id": article_id,
        "topic_key": topic_key,
        "source": vendor,
        "title": f"{vendor} item {article_id}",
        "content_length": content_length,
        "link": f"https://source.test/{article_id}",
    }


class DiversityFilterContractTests(unittest.TestCase):
    def test_deduplicates_below_limit_and_keeps_longest_content(self) -> None:
        articles = [
            article(1, "same", "Vendor A", content_length=10),
            article(2, "same", "Vendor B", content_length=20),
            article(3, "other", "Vendor C", content_length=5),
        ]
        before = copy.deepcopy(articles)
        logs: list[str] = []

        result = diversity_filter(articles, max_count=15, logger=logs.append)

        self.assertEqual([item["id"] for item in result], [2, 3])
        self.assertEqual(articles, before)
        self.assertTrue(any("topic=same" in line and "kept=2" in line for line in logs))

    def test_equal_content_lengths_keep_first_article_and_stable_topic_order(self) -> None:
        articles = [
            article(1, "same", "Vendor A", content_length=20),
            article(2, "other", "Vendor B", content_length=5),
            article(3, "same", "Vendor C", content_length=20),
        ]

        result = diversity_filter(articles, max_count=15)

        self.assertEqual([item["id"] for item in result], [1, 2])

    def test_empty_topic_key_fails_closed_with_summary_log(self) -> None:
        logs: list[str] = []

        result = diversity_filter(
            [article(1, "", "Google")],
            max_count=15,
            logger=logs.append,
        )

        self.assertEqual(result, [])
        self.assertEqual(len(logs), 1)
        self.assertIn("fail-closed", logs[0])
        self.assertIn("1", logs[0])

    def test_vendor_heavy_input_receives_actual_selection_penalty(self) -> None:
        articles = [
            article(1, "google-one", "Google"),
            article(2, "google-two", "Google"),
            article(3, "google-three", "Google"),
            article(4, "aws-one", "AWS"),
            article(5, "meta-one", "Meta"),
        ]

        result = diversity_filter(articles, max_count=3)

        self.assertEqual([item["id"] for item in result], [1, 4, 5])

    def test_repeated_vendors_are_deterministic_and_equal_penalties_are_stable(self) -> None:
        articles = [
            article(1, "google-one", "Google"),
            article(2, "google-two", "Google"),
            article(3, "aws-one", "AWS"),
            article(4, "aws-two", "AWS"),
            article(5, "meta-one", "Meta"),
        ]

        first = diversity_filter(articles, max_count=4)
        second = diversity_filter(copy.deepcopy(articles), max_count=4)

        self.assertEqual([item["id"] for item in first], [1, 3, 5, 2])
        self.assertEqual([item["id"] for item in second], [1, 3, 5, 2])

    def test_invalid_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            diversity_filter([], max_count=0)


class SelectedTopicContractTests(unittest.TestCase):
    @staticmethod
    def fake_core(selected_ids: list[int]) -> SimpleNamespace:
        return SimpleNamespace(
            diversity_filter=lambda articles, max_count=15: list(articles),
            _resolve_digest_selected_ids=Mock(return_value=selected_ids),
            log=Mock(),
        )

    def test_installed_resolver_fails_closed_on_duplicate_selected_topic(self) -> None:
        core = self.fake_core([1, 2, 3, 4, 5])
        install_diversity_contracts(core)
        articles = [
            article(1, "same", "One"),
            article(2, "same", "Two"),
            article(3, "three", "Three"),
            article(4, "four", "Four"),
            article(5, "five", "Five"),
        ]

        with self.assertRaisesRegex(RuntimeError, "max 1 per topic_key"):
            core._resolve_digest_selected_ids("markdown", [], articles, "ai")

    def test_installed_resolver_fails_closed_on_empty_selected_topic(self) -> None:
        core = self.fake_core([1])
        install_diversity_contracts(core)

        with self.assertRaisesRegex(RuntimeError, "trūkst topic_key"):
            core._resolve_digest_selected_ids(
                "markdown",
                [],
                [article(1, "", "One")],
                "agents",
            )

    def test_installed_resolver_accepts_unique_selected_topics(self) -> None:
        core = self.fake_core([1, 2, 3, 4, 5])
        install_diversity_contracts(core)
        articles = [article(index, f"topic-{index}", f"Vendor {index}") for index in range(1, 6)]

        selected = core._resolve_digest_selected_ids("markdown", [], articles, "devops")

        self.assertEqual(selected, [1, 2, 3, 4, 5])

    def test_install_is_idempotent(self) -> None:
        original = Mock(return_value=[1])
        core = SimpleNamespace(
            diversity_filter=lambda articles, max_count=15: list(articles),
            _resolve_digest_selected_ids=original,
            log=Mock(),
        )

        install_diversity_contracts(core)
        first_resolver = core._resolve_digest_selected_ids
        install_diversity_contracts(core)

        self.assertIs(core._resolve_digest_selected_ids, first_resolver)

    def test_digest_entrypoint_installs_contracts_into_legacy_core(self) -> None:
        env = os.environ.copy()
        env["HERMES_TECH_ROOT"] = str(ROOT)
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import digest; "
                    "print(digest._core.diversity_filter.__module__); "
                    "print(digest._core._resolve_digest_selected_ids.__module__)"
                ),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), ["digest_diversity", "digest_diversity"])


if __name__ == "__main__":
    unittest.main()
