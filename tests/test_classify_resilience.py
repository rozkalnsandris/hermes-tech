from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

import digest_classify_resilience as resilience


class ClassifyResilienceTests(unittest.TestCase):
    def make_core(self, classify):
        logs: list[str] = []
        core = SimpleNamespace(
            build_classify_user_prompt=lambda articles, known_events=None: "BASE JSON PROMPT",
            classify_batch=classify,
            log=logs.append,
        )
        return core, logs

    def test_prompt_lists_exact_allowed_ids_and_best_source_boundary(self) -> None:
        core, _ = self.make_core(lambda *args, **kwargs: ([], []))
        resilience.install_classify_resilience_contracts(core)

        prompt = core.build_classify_user_prompt(
            [{"id": 101}, {"id": 202}], known_events=None
        )

        self.assertIn("The only allowed article IDs", prompt)
        self.assertIn("[101, 202]", prompt)
        self.assertIn("Never invent, infer, renumber", prompt)
        self.assertIn("best_source_ids MUST also be a subset", prompt)

    def test_foreign_article_id_retries_same_batch_and_then_succeeds(self) -> None:
        calls = 0

        def classify(api_key, articles, known_events=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("article_id 4001 nav šī batch kandidātos")
            return ([{"article_ids": [101], "best_source_ids": [101]}], [])

        core, logs = self.make_core(classify)
        resilience.install_classify_resilience_contracts(core)

        with mock.patch.object(resilience.time, "sleep") as sleep:
            events, missing = core.classify_batch("key", [{"id": 101}])

        self.assertEqual(calls, 2)
        self.assertEqual(missing, [])
        self.assertEqual(events[0]["article_ids"], [101])
        self.assertTrue(any("CLASSIFY INVALID ID" in line for line in logs))
        self.assertTrue(any("CLASSIFY SEMANTIC RETRY 2/3" in line for line in logs))
        sleep.assert_called_once_with(1)

    def test_foreign_best_source_id_is_rejected_and_retried(self) -> None:
        calls = 0

        def classify(api_key, articles, known_events=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ([{"article_ids": [101], "best_source_ids": [999]}], [])
            return ([{"article_ids": [101], "best_source_ids": [101]}], [])

        core, _ = self.make_core(classify)
        resilience.install_classify_resilience_contracts(core)

        with mock.patch.object(resilience.time, "sleep"):
            events, missing = core.classify_batch("key", [{"id": 101}])

        self.assertEqual(calls, 2)
        self.assertEqual(missing, [])
        self.assertEqual(events[0]["best_source_ids"], [101])

    def test_non_identity_runtime_error_remains_fail_closed_without_outer_retry(self) -> None:
        calls = 0

        def classify(api_key, articles, known_events=None):
            nonlocal calls
            calls += 1
            raise RuntimeError("database locked")

        core, _ = self.make_core(classify)
        resilience.install_classify_resilience_contracts(core)

        with self.assertRaisesRegex(RuntimeError, "database locked"):
            core.classify_batch("key", [{"id": 101}])

        self.assertEqual(calls, 1)

    def test_foreign_id_exhaustion_still_fails_closed(self) -> None:
        calls = 0

        def classify(api_key, articles, known_events=None):
            nonlocal calls
            calls += 1
            raise RuntimeError("article_id 4001 nav šī batch kandidātos")

        core, logs = self.make_core(classify)
        resilience.install_classify_resilience_contracts(core)

        with mock.patch.object(resilience.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "article_id 4001"):
                core.classify_batch("key", [{"id": 101}])

        self.assertEqual(calls, resilience.SEMANTIC_MAX_ATTEMPTS)
        self.assertEqual(
            sum("CLASSIFY INVALID ID" in line for line in logs),
            resilience.SEMANTIC_MAX_ATTEMPTS,
        )

    def test_install_is_idempotent(self) -> None:
        calls = 0

        def classify(api_key, articles, known_events=None):
            nonlocal calls
            calls += 1
            return ([{"article_ids": [101], "best_source_ids": [101]}], [])

        core, _ = self.make_core(classify)
        resilience.install_classify_resilience_contracts(core)
        first = core.classify_batch
        resilience.install_classify_resilience_contracts(core)

        self.assertIs(core.classify_batch, first)
        core.classify_batch("key", [{"id": 101}])
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
