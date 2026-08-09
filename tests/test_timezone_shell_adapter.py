from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "tools" / "timezone_shell_adapter.py"
spec = importlib.util.spec_from_file_location("timezone_shell_adapter", ADAPTER_PATH)
assert spec and spec.loader
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class ShellAdapterUnitTests(unittest.TestCase):
    def test_publish_render_replaces_only_explicit_timezone_sites(self) -> None:
        source = '''#!/usr/bin/env bash
BASE=/tmp/root
PYTHON="$BASE/venv/bin/python"
DATE="${2:-$(date -u +%Y-%m-%d)}"
{
    echo "date: ${DATE}T07:00:00+02:00"
} > out
'''
        rendered = adapter.render("publish", source)
        self.assertIn('"$HERMES_TIME_PY" business-date', rendered)
        self.assertIn(
            '"$HERMES_TIME_PY" publication-timestamp "$DATE"', rendered
        )
        self.assertNotIn("date -u +%Y-%m-%d", rendered)
        self.assertNotIn("T07:00:00+02:00", rendered)

    def test_digest_runner_uses_business_date(self) -> None:
        rendered = adapter.render(
            "digest-runner",
            "TODAY=$(TZ=UTC date +%Y-%m-%d)\nprintf '%s' \"$TODAY\"\n",
        )
        self.assertIn('TODAY=$("$PYTHON" "$HERMES_TIME_PY" business-date)', rendered)
        self.assertNotIn("TZ=UTC", rendered)

    def test_source_drift_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "atrastas 0"):
            adapter.render("publish", "echo unchanged\n")


class PublishAdapterIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_obj = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_obj.cleanup)
        self.root = Path(self.tmp_obj.name)
        (self.root / "venv/bin").mkdir(parents=True)
        (self.root / "tools").mkdir()
        os.symlink(sys.executable, self.root / "venv/bin/python")
        for relative in (
            "hermes_time.py",
            "publish.sh",
            "run_digests.sh",
            "tools/timezone_shell_adapter.py",
        ):
            source = ROOT / relative
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            target.chmod(source.stat().st_mode)

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["HERMES_TECH_ROOT"] = str(self.root)
        env["HERMES_TIME_SOURCE_ROOT"] = str(self.root)
        return subprocess.run(
            ["bash", str(self.root / name), *args],
            cwd=self.root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_publish_front_matter_uses_winter_and_summer_offsets(self) -> None:
        (self.root / "publish_core.sh").write_text(
            '''#!/usr/bin/env bash
set -Eeuo pipefail
BASE="${HERMES_TECH_ROOT}"
PYTHON="$BASE/venv/bin/python"
DATE="${2:-$(date -u +%Y-%m-%d)}"
{
    echo "---"
    echo "date: ${DATE}T07:00:00+02:00"
    echo "---"
} > "$BASE/front-matter.txt"
''',
            encoding="utf-8",
        )
        for date_text, expected in (
            ("2026-01-15", "date: 2026-01-15T07:00:00+01:00"),
            ("2026-07-15", "date: 2026-07-15T07:00:00+02:00"),
            ("2026-03-29", "date: 2026-03-29T07:00:00+02:00"),
            ("2026-10-25", "date: 2026-10-25T07:00:00+01:00"),
        ):
            with self.subTest(date=date_text):
                proc = self.run_script("publish.sh", "devops", date_text)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(
                    expected,
                    (self.root / "front-matter.txt").read_text(encoding="utf-8"),
                )

    def test_runner_render_removes_utc_business_date(self) -> None:
        (self.root / "run_digests_core.sh").write_text(
            '''#!/usr/bin/env bash
set -Eeuo pipefail
BASE="${HERMES_TECH_ROOT}"
PYTHON="$BASE/venv/bin/python"
TODAY=$(TZ=UTC date +%Y-%m-%d)
printf '%s\n' "$TODAY" > "$BASE/business-date.txt"
''',
            encoding="utf-8",
        )
        # This fixture validates only the timezone shell adapter. Use the
        # explicit check path so production-only readiness checks do not turn
        # an isolated rendering fixture into an infrastructure integration test.
        proc = self.run_script("run_digests.sh", "--check")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = (self.root / "business-date.txt").read_text(encoding="utf-8").strip()
        expected = subprocess.run(
            [sys.executable, str(self.root / "hermes_time.py"), "business-date"],
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout.strip()
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
