from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "notify_pipeline_failure.py"
SPEC = importlib.util.spec_from_file_location("notify_pipeline_failure", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
notify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notify)


class PipelineFailureNotifyTests(unittest.TestCase):
    def test_reads_only_bytes_appended_by_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "digest-cron.log"
            old = "old run\nTelegram pipeline kopsavilkums nosūtīts\n"
            new = "new run\nRuntimeError: article_id 4001 nav šī batch kandidātos\n"
            path.write_text(old + new, encoding="utf-8")

            segment = notify.read_appended_log(path, len(old.encode("utf-8")))

        self.assertEqual(segment, new)
        self.assertFalse(notify.normal_summary_was_sent(segment))

    def test_normal_summary_marker_suppresses_fallback(self) -> None:
        text = "x\nTelegram pipeline kopsavilkums nosūtīts\ny\n"
        self.assertTrue(notify.normal_summary_was_sent(text))

    def test_exception_detail_wins_over_generic_runner_error(self) -> None:
        text = "\n".join(
            [
                "Traceback (most recent call last):",
                "RuntimeError: article_id 4001 nav šī batch kandidātos",
                "2026-08-08 [digest-runner] KĻŪDA: globalā klasifikācija neizdevās (rc=1)",
            ]
        )
        self.assertEqual(
            notify.extract_failure_detail(text),
            "RuntimeError: article_id 4001 nav šī batch kandidātos",
        )

    def test_generic_error_is_used_when_no_exception_line_exists(self) -> None:
        text = "2026-08-08 [digest-runner] KĻŪDA: timeout"
        self.assertEqual(notify.extract_failure_detail(text), text)

    def test_missing_log_returns_safe_generic_detail(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "missing.log"
            segment = notify.read_appended_log(path, 0)
        self.assertEqual(segment, "")
        self.assertIn("pipeline exited", notify.extract_failure_detail(segment))

    def test_runner_invokes_notifier_only_after_full_run_failure(self) -> None:
        runner = (ROOT / "run_digests.sh").read_text(encoding="utf-8")
        self.assertIn("if (( $# > 0 )); then", runner)
        self.assertIn('exec bash -c "$PATCHED" "$CORE" "$@"', runner)
        self.assertIn('bash -c "$PATCHED" "$CORE"', runner)
        self.assertIn("if (( rc != 0 )); then", runner)
        self.assertIn("tools/notify_pipeline_failure.py", runner)
        self.assertIn('exit "$rc"', runner)


if __name__ == "__main__":
    unittest.main()
