from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DigestEntrypointContractTests(unittest.TestCase):
    def test_digest_response_resilience_is_installed_before_core_export(self) -> None:
        text = (ROOT / "digest.py").read_text(encoding="utf-8")
        install = "_install_digest_response_resilience_contracts()"
        export = "_export_core_api()"
        self.assertIn("digest_response_resilience", text)
        self.assertIn(install, text)
        self.assertLess(text.index(install), text.rindex(export))


if __name__ == "__main__":
    unittest.main()
