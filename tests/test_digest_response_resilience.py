from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest import mock

import digest_response_resilience as resilience


class DigestResponseResilienceTests(unittest.TestCase):
    def make_core(self, responses):
        calls: list[str] = []
        logs: list[str] = []
        queue = list(responses)

        def call_deepseek(api_key: str, system: str, user: str) -> str:
            calls.append(user)
            value = queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return json.dumps(value)

        def extract_sources(markdown: str):
            return [line for line in markdown.splitlines() if line.startswith("[Source ")]

        core = SimpleNamespace(
            call_deepseek=call_deepseek,
            _extract_digest_source_candidates=extract_sources,
            DIGEST_ITEM_COUNT=5,
            log=logs.append,
        )
        return core, calls, logs

    def valid_digest(self):
        return {
            "selected_ids": [1, 2, 3, 4, 5],
            "digest": "\n".join(
                f"[Source {index}](https://example.com/{index})"
                for index in range(1, 6)
            ),
        }

    def test_four_source_links_retry_then_succeed(self) -> None:
        invalid = self.valid_digest()
        invalid["digest"] = "\n".join(
            f"[Source {index}](https://example.com/{index})"
            for index in range(1, 5)
        )
        core, calls, logs = self.make_core([invalid, self.valid_digest()])
        resilience.install_digest_response_resilience(core)

        with mock.patch.object(resilience.time, "sleep") as sleep:
            raw = core.call_deepseek("key", "system", "user")

        self.assertEqual(json.loads(raw)["selected_ids"], [1, 2, 3, 4, 5])
        self.assertEqual(len(calls), 2)
        self.assertIn("source link count=4, expected=5", calls[1])
        self.assertTrue(any("semantic mismatch" in line for line in logs))
        self.assertTrue(any("semantic retry izdevās" in line for line in logs))
        sleep.assert_called_once_with(1)

    def test_four_selected_ids_retry_then_succeed(self) -> None:
        invalid = self.valid_digest()
        invalid["selected_ids"] = [1, 2, 3, 4]
        core, calls, _ = self.make_core([invalid, self.valid_digest()])
        resilience.install_digest_response_resilience(core)

        with mock.patch.object(resilience.time, "sleep"):
            core.call_deepseek("key", "system", "user")

        self.assertEqual(len(calls), 2)
        self.assertIn("selected_ids count=4, expected=5", calls[1])

    def test_non_digest_json_is_not_retried(self) -> None:
        payload = {"events": [{"article_ids": [1]}]}
        core, calls, _ = self.make_core([payload])
        resilience.install_digest_response_resilience(core)

        raw = core.call_deepseek("key", "system", "classify")

        self.assertEqual(json.loads(raw), payload)
        self.assertEqual(len(calls), 1)

    def test_underlying_runtime_error_is_not_semantically_retried(self) -> None:
        core, calls, _ = self.make_core([RuntimeError("transport failed")])
        resilience.install_digest_response_resilience(core)

        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            core.call_deepseek("key", "system", "user")

        self.assertEqual(len(calls), 1)

    def test_semantic_exhaustion_fails_closed(self) -> None:
        invalid = self.valid_digest()
        invalid["digest"] = "[Source 1](https://example.com/1)"
        core, calls, _ = self.make_core(
            [invalid, invalid, invalid]
        )
        resilience.install_digest_response_resilience(core)

        with mock.patch.object(resilience.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "semantic retry izsmelts"):
                core.call_deepseek("key", "system", "user")

        self.assertEqual(len(calls), resilience.SEMANTIC_MAX_ATTEMPTS)

    def test_install_is_idempotent(self) -> None:
        core, calls, _ = self.make_core([self.valid_digest()])
        resilience.install_digest_response_resilience(core)
        first = core.call_deepseek
        resilience.install_digest_response_resilience(core)

        self.assertIs(core.call_deepseek, first)
        core.call_deepseek("key", "system", "user")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
