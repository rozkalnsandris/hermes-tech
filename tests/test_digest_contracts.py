from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import digest_core


class FakeResponse:
    def __init__(
        self,
        payload: object | None = None,
        *,
        status_code: int = 200,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self._json_error = json_error
        self.ok = 200 <= status_code < 300

    def json(self) -> object:
        if self._json_error is not None:
            raise self._json_error
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


def deepseek_envelope(content: str, finish_reason: str | None = "stop") -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "prompt_cache_hit_tokens": 0,
            "completion_tokens": 20,
        },
    }


class DeepSeekJsonContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = (
            'Return exactly {"selected_ids": [1], "digest": "markdown"}'
        )

    def call(self, responses: list[object]) -> tuple[dict, Mock]:
        post = Mock(side_effect=responses)
        with (
            patch.object(digest_core.requests, "post", post),
            patch.object(digest_core.time, "sleep"),
            patch.object(digest_core, "log"),
        ):
            raw = digest_core.call_deepseek("test-key", "system", self.user)
        return json.loads(raw), post

    def test_valid_json_object_and_fences_are_normalised(self) -> None:
        payload, post = self.call(
            [
                FakeResponse(
                    deepseek_envelope(
                        '```json\n{"selected_ids":[1],"digest":"ok"}\n```'
                    )
                )
            ]
        )
        self.assertEqual(payload, {"selected_ids": [1], "digest": "ok"})
        self.assertEqual(post.call_count, 1)
        request_json = post.call_args.kwargs["json"]
        self.assertEqual(request_json["response_format"], {"type": "json_object"})
        self.assertEqual(request_json["thinking"], {"type": "disabled"})

    def test_malformed_json_retries_then_succeeds(self) -> None:
        payload, post = self.call(
            [
                FakeResponse(deepseek_envelope("{")),
                FakeResponse(
                    deepseek_envelope('{"selected_ids":[2],"digest":"fixed"}')
                ),
            ]
        )
        self.assertEqual(payload["selected_ids"], [2])
        self.assertEqual(post.call_count, 2)
        retry_user = post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertIn("JSON RETRY REQUIREMENT", retry_user)

    def test_schema_mismatch_retries_then_succeeds(self) -> None:
        payload, post = self.call(
            [
                FakeResponse(
                    deepseek_envelope(
                        '{"selected_ids":"not-a-list","digest":"bad"}'
                    )
                ),
                FakeResponse(
                    deepseek_envelope('{"selected_ids":[3],"digest":"good"}')
                ),
            ]
        )
        self.assertEqual(payload["digest"], "good")
        self.assertEqual(post.call_count, 2)

    def test_length_finish_reason_fails_without_identical_retry(self) -> None:
        post = Mock(
            return_value=FakeResponse(
                deepseek_envelope("{}", finish_reason="length")
            )
        )
        with (
            patch.object(digest_core.requests, "post", post),
            patch.object(digest_core.time, "sleep"),
            patch.object(digest_core, "log"),
            self.assertRaisesRegex(RuntimeError, "nogriezts"),
        ):
            digest_core.call_deepseek("test-key", "system", self.user)
        self.assertEqual(post.call_count, 1)

    def test_non_retryable_http_error_fails_immediately(self) -> None:
        post = Mock(return_value=FakeResponse(status_code=400, text="bad request"))
        with (
            patch.object(digest_core.requests, "post", post),
            patch.object(digest_core.time, "sleep"),
            patch.object(digest_core, "log"),
            self.assertRaisesRegex(RuntimeError, "non-retryable HTTP 400"),
        ):
            digest_core.call_deepseek("test-key", "system", self.user)
        self.assertEqual(post.call_count, 1)

    def test_transport_failure_exhausts_bounded_retries(self) -> None:
        post = Mock(side_effect=requests.Timeout("synthetic timeout"))
        with (
            patch.object(digest_core.requests, "post", post),
            patch.object(digest_core.time, "sleep"),
            patch.object(digest_core, "log"),
            self.assertRaisesRegex(RuntimeError, "transport"),
        ):
            digest_core.call_deepseek("test-key", "system", self.user)
        self.assertEqual(post.call_count, digest_core.DEEPSEEK_JSON_MAX_ATTEMPTS)


class SelectedIdReconciliationTests(unittest.TestCase):
    @staticmethod
    def articles() -> list[dict]:
        return [
            {
                "id": index,
                "title": f"Canonical release {index}",
                "link": f"https://source.test/{index}",
            }
            for index in range(1, 6)
        ]

    @staticmethod
    def markdown(urls: list[str] | None = None, labels: list[str] | None = None) -> str:
        urls = urls or [f"https://source.test/{index}" for index in range(1, 6)]
        labels = labels or [f"Canonical release {index}" for index in range(1, 6)]
        blocks = []
        for index, (label, url) in enumerate(zip(labels, urls), 1):
            if index % 2:
                blocks.append(f"Source: [{label}]({url})")
            else:
                blocks.append(f"[{label}]({url})")
        return "\n\n".join(blocks)

    def test_actual_sources_repair_stale_invalid_and_reordered_model_ids(self) -> None:
        with patch.object(digest_core, "log"):
            resolved = digest_core._resolve_digest_selected_ids(
                self.markdown(),
                [5, "4", 999, True, "not-an-id", 1],
                self.articles(),
                "agents",
            )
        self.assertEqual(resolved, [1, 2, 3, 4, 5])

    def test_wrong_source_count_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Source atlase nav 5"):
            digest_core._resolve_digest_selected_ids(
                "[One](https://source.test/1)",
                [1],
                self.articles(),
                "ai",
            )

    def test_ambiguous_label_without_exact_url_fails_closed(self) -> None:
        articles = self.articles()
        articles[0]["title"] = "Alpha release notes"
        articles[1]["title"] = "Beta release notes"
        labels = ["release notes", *[f"Canonical release {i}" for i in range(2, 6)]]
        urls = ["https://wrong.test/ambiguous", *[f"https://source.test/{i}" for i in range(2, 6)]]
        with self.assertRaisesRegex(RuntimeError, "nevar unikāli sasaistīt"):
            digest_core._resolve_digest_selected_ids(
                self.markdown(urls, labels),
                [],
                articles,
                "devops",
            )

    def test_duplicate_candidate_id_fails_closed(self) -> None:
        articles = self.articles()
        articles.append(dict(articles[0]))
        with self.assertRaisesRegex(RuntimeError, "atkārtojas ID 1"):
            digest_core._resolve_digest_selected_ids(
                self.markdown(),
                [1, 2, 3, 4, 5],
                articles,
                "devops",
            )


class SourceRestorationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.root = Path(self.tmp_obj.name)
        self.db = self.root / "hermes.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(
            """
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                link TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO articles(id, title, link) VALUES (?, ?, ?)",
            [
                (index, f"[Vendor] Canonical title {index}", f"https://canonical.test/{index}")
                for index in range(1, 6)
            ],
        )
        conn.commit()
        conn.close()

    def write_digest(self, body: str) -> Path:
        path = self.root / "digest.md"
        path.write_text(body, encoding="utf-8")
        return path

    def test_restores_canonical_db_links_atomically_in_mixed_formats(self) -> None:
        path = self.write_digest(
            """<!-- selected_ids: 1,2,3,4,5 -->
### One
Summary Source: [Canonical title 1](https://model.test/one)

### Two
[Canonical title 2](https://model.test/two)

### Three
Source: [Canonical title 3](https://model.test/three)

### Four
[Canonical title 4](https://model.test/four)

### Five
Source: [Canonical title 5](https://model.test/five)
"""
        )
        path.chmod(0o640)
        with patch.object(digest_core, "DB", self.db):
            digest_core._restore_digest_source_links(path)

        text = path.read_text(encoding="utf-8")
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
        self.assertIn("Summary\n\n[Vendor: Canonical title 1](https://canonical.test/1)", text)
        for index in range(1, 6):
            self.assertIn(
                f"[Vendor: Canonical title {index}](https://canonical.test/{index})",
                text,
            )
        self.assertNotIn("https://model.test/", text)
        self.assertFalse(path.with_name(path.name + ".source-restore.tmp").exists())

    def test_duplicate_selected_ids_fail_before_file_replacement(self) -> None:
        path = self.write_digest(
            "<!-- selected_ids: 1,1 -->\n[One](https://canonical.test/1)\n"
        )
        before = path.read_bytes()
        with (
            patch.object(digest_core, "DB", self.db),
            self.assertRaisesRegex(RuntimeError, "satur dublikātus"),
        ):
            digest_core._restore_digest_source_links(path)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_database_row_fails_without_modifying_digest(self) -> None:
        path = self.write_digest(
            """<!-- selected_ids: 1,2,3,4,99 -->
[One](https://canonical.test/1)
[Two](https://canonical.test/2)
[Three](https://canonical.test/3)
[Four](https://canonical.test/4)
[Missing](https://canonical.test/99)
"""
        )
        before = path.read_bytes()
        with (
            patch.object(digest_core, "DB", self.db),
            self.assertRaisesRegex(RuntimeError, "DB trūkst selected article ID"),
        ):
            digest_core._restore_digest_source_links(path)
        self.assertEqual(path.read_bytes(), before)

    def test_ambiguous_source_mapping_fails_without_partial_rewrite(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE articles SET title='Shared release' WHERE id IN (1,2)")
        conn.commit()
        conn.close()
        path = self.write_digest(
            """<!-- selected_ids: 1,2,3,4,5 -->
[Shared release](https://wrong.test/ambiguous)
[Canonical title 2](https://wrong.test/two)
[Canonical title 3](https://canonical.test/3)
[Canonical title 4](https://canonical.test/4)
[Canonical title 5](https://canonical.test/5)
"""
        )
        before = path.read_bytes()
        with (
            patch.object(digest_core, "DB", self.db),
            self.assertRaisesRegex(RuntimeError, "nevar unikāli sasaistīt"),
        ):
            digest_core._restore_digest_source_links(path)
        self.assertEqual(path.read_bytes(), before)


class QualityAndRoutingValidatorTests(unittest.TestCase):
    @staticmethod
    def valid_analysis() -> str:
        sentences = []
        for index in range(3):
            words = [f"word{index}_{number}" for number in range(20)]
            sentences.append(" ".join(words) + ".")
        return " ".join(sentences)

    def test_analysis_validator_accepts_hard_limits(self) -> None:
        markdown = f"> 💬 Hermes: {self.valid_analysis()}\n"
        self.assertEqual(digest_core.validate_hermes_analyses(markdown, 1), [])

    def test_analysis_validator_rejects_short_and_count_mismatch(self) -> None:
        markdown = "> 💬 Hermes: Too short.\n"
        issues = digest_core.validate_hermes_analyses(markdown, 2)
        self.assertTrue(any("selected_ids skaits" in issue for issue in issues))
        self.assertTrue(any("vārdi" in issue for issue in issues))
        self.assertTrue(any("teikumi" in issue for issue in issues))

    def test_style_validator_rejects_template_phrase_and_list(self) -> None:
        markdown = (
            "> 💬 Hermes: This development highlights a risk.\n"
            "> - First point\n"
        )
        issues = digest_core.validate_hermes_style(markdown)
        self.assertTrue(any("šabloniska AI frāze" in issue for issue in issues))
        self.assertTrue(any("ne sarakstam" in issue for issue in issues))

    def test_cross_category_conflict_fails_closed_and_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = digest_core.datetime.now(digest_core.timezone.utc).strftime("%Y-%m-%d")
            manifest = root / f"{today}-routing.json"
            manifest.write_text(
                json.dumps(
                    {
                        "events": [
                            {"topic_key": "same-event", "primary_category": "ai"},
                            {"topic_key": "same-event", "primary_category": "agents"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            notify = Mock(return_value=True)
            with (
                patch.object(digest_core, "RUNS", root),
                patch.object(digest_core, "LOG", root / "digest.log"),
                patch.object(digest_core, "load_env", return_value={}),
                patch.object(digest_core, "send_telegram", notify),
            ):
                self.assertEqual(digest_core.step_validate(""), 1)
            notify.assert_called_once()

    def test_cross_category_validation_ignores_reject_and_accepts_unique_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = digest_core.datetime.now(digest_core.timezone.utc).strftime("%Y-%m-%d")
            (root / f"{today}-routing.json").write_text(
                json.dumps(
                    {
                        "events": [
                            {"topic_key": "one", "primary_category": "devops"},
                            {"topic_key": "one", "primary_category": "reject"},
                            {"topic_key": "two", "primary_category": "ai"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(digest_core, "RUNS", root),
                patch.object(digest_core, "LOG", root / "digest.log"),
            ):
                self.assertEqual(digest_core.step_validate(""), 0)


class TopicAndDiversityTests(unittest.TestCase):
    def test_exact_identity_merges_article_and_source_ids(self) -> None:
        events = [
            {
                "topic_key": "tool-v1-release",
                "primary_category": "devops",
                "primary_entity": "tool",
                "event_type": "release",
                "version": "1.0",
                "article_ids": [1],
                "best_source_ids": [1],
                "confidence": 0.8,
            },
            {
                "topic_key": "tool-release-v1",
                "primary_category": "devops",
                "primary_entity": "tool",
                "event_type": "release",
                "version": "1.0",
                "article_ids": [2],
                "best_source_ids": [2],
                "confidence": 0.9,
            },
        ]
        with patch.object(digest_core, "log"):
            merged = digest_core.global_reconciliation(events)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["article_ids"], [1, 2])
        self.assertEqual(merged[0]["best_source_ids"], [1, 2])

    def test_different_explicit_versions_never_merge(self) -> None:
        events = [
            {
                "topic_key": "tool-release",
                "primary_category": "devops",
                "primary_entity": "tool",
                "event_type": "release",
                "version": "1.0",
                "article_ids": [1],
                "best_source_ids": [1],
            },
            {
                "topic_key": "tool-new-release",
                "primary_category": "devops",
                "primary_entity": "tool",
                "event_type": "release",
                "version": "2.0",
                "article_ids": [2],
                "best_source_ids": [2],
            },
        ]
        with patch.object(digest_core, "log"):
            merged = digest_core.global_reconciliation(events)
        self.assertEqual(len(merged), 2)

    @unittest.expectedFailure
    def test_known_issue_5_duplicate_topic_is_removed_even_below_limit(self) -> None:
        articles = [
            {"id": 1, "topic_key": "same", "content_length": 10},
            {"id": 2, "topic_key": "same", "content_length": 20},
            {"id": 3, "topic_key": "other", "content_length": 5},
        ]
        result = digest_core.diversity_filter(articles, max_count=15)
        self.assertEqual([item["id"] for item in result], [2, 3])


if __name__ == "__main__":
    unittest.main()
