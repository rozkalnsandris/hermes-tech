#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "run_digests_core.sh"
FAKE_URL = "https://hc-ping.example/12345678-1234-1234-1234-123456789abc"


def extract_shell_function(name: str) -> str:
    source = RUNNER.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
        source,
    )
    if not match:
        raise AssertionError(f"missing shell function {name}")
    return match.group(0)


class HealthcheckArgvTests(unittest.TestCase):
    def test_healthcheck_url_is_sent_via_stdin_config_not_curl_argv(self) -> None:
        function = extract_shell_function("ping_healthcheck")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            fake_curl = tmp / "curl"
            args_path = tmp / "args.txt"
            stdin_path = tmp / "stdin.txt"

            fake_curl.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -Eeuo pipefail
                    printf '%s\n' "$@" > "$HERMES_TEST_CURL_ARGS"
                    cat > "$HERMES_TEST_CURL_STDIN"
                    """
                ),
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)

            script = (
                "set -Eeuo pipefail\n"
                "log() { :; }\n"
                f"{function}\n"
                f"ping_healthcheck {FAKE_URL!r} /start\n"
            )
            env = os.environ.copy()
            env["PATH"] = f"{tmp}:{env['PATH']}"
            env["HERMES_TEST_CURL_ARGS"] = str(args_path)
            env["HERMES_TEST_CURL_STDIN"] = str(stdin_path)

            subprocess.run(
                ["bash", "-c", script],
                check=True,
                cwd=REPO,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            argv = args_path.read_text(encoding="utf-8")
            stdin = stdin_path.read_text(encoding="utf-8")

        self.assertNotIn(FAKE_URL, argv)
        self.assertNotIn("12345678-1234-1234-1234-123456789abc", argv)
        for required in (
            "--disable",
            "--config",
            "-",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "15",
            "--retry",
            "2",
            "--retry-all-errors",
        ):
            self.assertIn(required, argv)

        self.assertEqual(
            stdin,
            f'url = "{FAKE_URL}/start"\n',
        )

    def test_source_has_no_direct_healthcheck_url_argument(self) -> None:
        function = extract_shell_function("ping_healthcheck")
        self.assertIn("curl --disable --config -", function)
        self.assertNotIn('"$url$suffix"', function)
        self.assertNotIn('"${url}${suffix}"', function)


if __name__ == "__main__":
    unittest.main()
