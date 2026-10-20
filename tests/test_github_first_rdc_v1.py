"""GitHub-first / bounded RDC-fallback source-only consumer conformance."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REV = "4e89b75d7f496dfb43fd9c1eb10a995fad53fdd8"


def select(c):
    if c["error"]:
        return "STOP"
    if c["github"]:
        return "GITHUB"
    if c["ci"]:
        return "GITHUB_ACTIONS"
    return "RDC_EXCEPTION" if c["host"] and c["authority"] else "STOP"


class GitHubFirstRdcConformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads((ROOT / ".github/github-first-rdc-v1.json").read_text())
        cls.agents = (ROOT / "AGENTS.md").read_text()
        cls.guide = (ROOT / "docs/GITHUB_FIRST_RDC_ADOPTION_V1.md").read_text()
        cls.deploy = (ROOT / ".github/workflows/simple-deploy.yml").read_text()

    def test_exact_immutable_contract(self):
        self.assertEqual(self.policy["schema"], "rozkalns.github-first-rdc-fallback.consumer.v1")
        self.assertEqual(self.policy["repository"], "rozkalnsandris/hermes-tech")
        self.assertEqual(self.policy["upstream"]["revision"], REV)
        self.assertEqual(len(REV), 40)
        self.assertIn(REV, self.agents)

    def test_routing(self):
        p = self.policy["selection"]
        for key in ("mandatory_per_step", "github_when_equivalent", "github_actions_when_applicable"):
            self.assertIs(p[key], True)
        self.assertIs(p["rdc_for_convenience"], False)
        self.assertEqual(p["order"], [
            "GITHUB_CONNECTED_TOOLS", "GITHUB_HOSTED_CI_WHEN_APPLICABLE",
            "MINIMAL_AUTHORIZED_RDC_IF_GITHUB_INCAPABLE", "STOP",
        ])

    def test_exception_proof_and_fail_closed(self):
        for k, v in self.policy["rdc_exception"].items():
            if k != "evidence_fields":
                self.assertIs(v, True, k)
        self.assertEqual(len(self.policy["rdc_exception"]["evidence_fields"]), 8)
        self.assertEqual(len(set(self.policy["rdc_exception"]["evidence_fields"])), 8)
        for k, v in self.policy["fail_closed"].items():
            self.assertIs(v, True, k)

    def test_cases(self):
        rows = self.policy["cases"]
        self.assertEqual(len(rows), 8)
        self.assertEqual(len(set(c["name"] for c in rows)), len(rows))
        for c in rows:
            with self.subTest(c["name"]):
                self.assertEqual(select(c), c["expected"])

    def test_stricter_editorial_and_live_boundaries(self):
        for k, v in self.policy["local_stricter"].items():
            self.assertIs(v, True, k)
        self.assertIn("Existing Hermes Tech editorial and production safety rules remain stricter", self.agents)
        self.assertIn("Merge remains explicit owner authority", self.agents)
        self.assertIn("GitHub -> minimal RDC -> GitHub", self.guide)

    def test_governance_cannot_trigger_deploy(self):
        expected = self.policy["simple_deploy"]["allowed_trigger_paths"]
        self.assertEqual(expected, ["site/**", "Dockerfile", "deploy/**", ".simple-deploy.json"])
        self.assertIs(self.policy["simple_deploy"]["governance_changes_trigger_deploy"], False)
        for path in expected:
            self.assertIn("'" + path + "'", self.deploy)
        self.assertIn("on:\n  push:\n", self.deploy)
        self.assertNotIn("paths-ignore:", self.deploy)
        self.assertNotIn("'AGENTS.md'", self.deploy)
        self.assertNotIn("'docs/**'", self.deploy)
        self.assertNotIn("'.github/**'", self.deploy)


if __name__ == "__main__":
    unittest.main()
