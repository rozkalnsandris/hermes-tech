#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
ENV_EXAMPLE = REPO / ".env.example"
TRANSPARENCY = REPO / "docs" / "transparency-contract.md"
HOW = REPO / "site" / "content" / "how-hermes-works.md"
LLMS = REPO / "site" / "static" / "llms.txt"
FOOTER = REPO / "site" / "layouts" / "_default" / "baseof.html"
DIGEST_CORE = REPO / "digest_core.py"

PUBLIC_SURFACES = (HOW, LLMS, FOOTER)
EXPECTED_DOTENV_KEYS = {
    "DEEPSEEK_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "HEALTHCHECK_URL",
}


def source_constant(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                value = node.value
                return ast.literal_eval(value)
    raise AssertionError(f"missing constant {name} in {path}")


def normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def dotenv_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AssertionError(f"invalid .env.example line: {raw!r}")
        key, value = line.split("=", 1)
        entries[key.strip()] = value.strip()
    return entries


class DocumentationContractTests(unittest.TestCase):
    def test_readme_supports_secretless_read_only_reproduction(self) -> None:
        text = README.read_text(encoding="utf-8")
        required = (
            "## Architecture",
            "## Repository layout",
            "## Supported toolchain",
            "## Reproduce read-only validation",
            "## Runtime configuration",
            "## Runtime and scheduling map",
            "## Generated-content Git policy",
            "## Database, backup, and recovery",
            "## Contribution workflow",
            "python3.11 -m venv /tmp/hermes-tech-venv",
            "bash tools/install_hugo.sh /tmp/hermes-tech-hugo/bin",
            'export HERMES_TECH_ROOT="$PWD"',
            "bash tools/ci.sh",
            "does not fetch every linked article page",
            "does **not** mean every automatically passing digest receives manual approval",
        )
        for marker in required:
            self.assertIn(marker, text)

        self.assertNotIn("/home/andris/hermes-tech/data/hermes.db", text)
        self.assertNotRegex(text, r"(?i)(api[_ -]?key|token)\s*[=:]\s*['\"]?[A-Za-z0-9_-]{16,}")

    def test_dotenv_example_is_complete_used_and_value_free(self) -> None:
        entries = dotenv_entries()
        self.assertEqual(set(entries), EXPECTED_DOTENV_KEYS)
        self.assertTrue(all(value == "" for value in entries.values()))

        digest_source = DIGEST_CORE.read_text(encoding="utf-8")
        runner_source = (REPO / "run_digests_core.sh").read_text(encoding="utf-8")
        combined = digest_source + "\n" + runner_source
        for key in EXPECTED_DOTENV_KEYS:
            self.assertIn(key, combined)

        runtime_source = (REPO / "hermes_runtime.py").read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        self.assertIn("HERMES_TECH_ROOT", runtime_source)
        self.assertIn("HERMES_TECH_ROOT", readme)
        self.assertIn("process environment variable, not a `.env` setting", readme)

    def test_public_ingestion_claim_matches_collector(self) -> None:
        collector = (REPO / "collector_core.py").read_text(encoding="utf-8")
        self.assertIn('getattr(entry, "content", None)', collector)
        self.assertIn('getattr(entry, "summary", "")', collector)
        self.assertNotIn("requests.get", collector)

        how = HOW.read_text(encoding="utf-8")
        llms = LLMS.read_text(encoding="utf-8")
        for text in (how, llms):
            self.assertIn("entry.content", text)
            self.assertIn("RSS summary", text)
            self.assertRegex(text, r"does not (?:download|fetch)\s+every linked\s+article page")

    def test_model_and_item_count_follow_executable_constants(self) -> None:
        model = source_constant(DIGEST_CORE, "DEEPSEEK_MODEL")
        item_count = source_constant(DIGEST_CORE, "DIGEST_ITEM_COUNT")
        self.assertIsInstance(model, str)
        self.assertIsInstance(item_count, int)

        for path in (HOW, LLMS):
            normalized = normalized_words(path.read_text(encoding="utf-8"))
            self.assertIn(normalized_words(model), normalized, path)

        how = HOW.read_text(encoding="utf-8")
        llms = LLMS.read_text(encoding="utf-8")
        self.assertIn(f"selects {item_count} items", how)
        self.assertEqual(llms.count(f"({item_count} selected items)"), 3)

    def test_public_review_boundary_and_automatic_publish_are_explicit(self) -> None:
        runner = (REPO / "run_digests_core.sh").read_text(encoding="utf-8")
        self.assertIn("PUBLISH SUCCESSFUL CATEGORIES", runner)
        self.assertIn('"$PYTHON" "$BASE/digest.py" publish', runner)

        how = HOW.read_text(encoding="utf-8")
        llms = LLMS.read_text(encoding="utf-8")
        footer = FOOTER.read_text(encoding="utf-8")
        self.assertIn("does **not** require Andris to approve every digest", how)
        self.assertIn("may be published automatically", llms)
        self.assertIn("without per-run human approval", footer)

    def test_stale_fixed_claims_are_absent_from_public_surfaces(self) -> None:
        stale_literals = (
            "full article text is stored",
            "human-supervised digests",
            "waits for Andris to review it",
            "rss(50)",
            "~50 RSS sources",
            "under €1 per month",
            "under 1 EUR/month",
        )
        fixed_source_count = re.compile(r"(?i)(?:about|approximately|~)?\s*\d+\s+RSS sources")
        fixed_cost = re.compile(r"(?i)(?:under|less than|about|approximately|~)\s*[€$]?\d+(?:[.,]\d+)?\s*(?:EUR|euro|€)?\s*/?\s*month")

        for path in PUBLIC_SURFACES:
            text = path.read_text(encoding="utf-8")
            lowered = text.casefold()
            for stale in stale_literals:
                self.assertNotIn(stale.casefold(), lowered, path)
            self.assertIsNone(fixed_source_count.search(text), path)
            self.assertIsNone(fixed_cost.search(text), path)

    def test_transparency_contract_names_sources_and_update_path(self) -> None:
        text = TRANSPARENCY.read_text(encoding="utf-8")
        required = (
            "collector_core.py::entry_texts()",
            "digest_core.py::DEEPSEEK_MODEL",
            "digest_core.py::DIGEST_ITEM_COUNT",
            "run_digests_core.sh",
            "configured RSS feeds",
            "manual approval is not required for every run",
            "API cost varies",
            "tests/test_documentation_contract.py",
            "bash tools/ci.sh",
        )
        for marker in required:
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
