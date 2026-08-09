from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "publisher-credential-boundary.md"
SYNC = ROOT / "sync_generated_content.sh"
POLICY = ROOT / "tools" / "configure_github_main_policy.py"
PUBLISH_READINESS = ROOT / "tools" / "publication_readiness.py"
PULL_DEPLOY = ROOT / "tools" / "pull-deploy" / "release" / "hermes-tech-pull-deploy"


def normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


class PublisherCredentialBoundaryTests(unittest.TestCase):
    def test_document_records_confirmed_residual_risk_and_owner(self) -> None:
        text = normalized(DOC)
        for marker in (
            "least-privilege design gap, not an incident finding",
            "same-UID arbitrary code execution",
            "not forced through `sync_generated_content.sh`",
            "`rozkalnsandris/RPi5_main#110`",
            "one narrow publication operation",
            "HERMES_TECH_DEPLOY_REQUIRED=no",
            "RPI5_MAIN_CHANGE_REQUIRED=yes",
        ):
            self.assertIn(marker, text)

    def test_normal_publisher_keeps_exact_generated_path_and_sha_guards(self) -> None:
        source = SYNC.read_text(encoding="utf-8")
        for marker in (
            "readonly -a COMMIT_PATHS=",
            '[[ "$parent_sha" == "$expected_base" ]]',
            '[[ "$commit_subject" == "Publish $category digest $digest_date" ]]',
            'is_commit_path "$path"',
            '"$expected_commit:refs/heads/$BRANCH"',
            '[[ "$remote_sha" == "$expected_commit" ]]',
            '[[ "$local_sha" == "$remote_sha" ]]',
        ):
            self.assertIn(marker, source)

        self.assertNotIn("git push --force", source)
        self.assertNotIn("git push -f", source)

    def test_integrity_ruleset_has_no_bypass_while_code_gate_deploy_key_does(self) -> None:
        source = POLICY.read_text(encoding="utf-8")
        integrity_start = source.index("def integrity_ruleset_payload")
        code_start = source.index("def code_gate_ruleset_payload")
        repo_settings_start = source.index("def repository_settings_payload")

        integrity = source[integrity_start:code_start]
        code_gate = source[code_start:repo_settings_start]

        self.assertIn('"bypass_actors": []', integrity)
        self.assertIn('"actor_type": "DeployKey"', code_gate)
        self.assertIn('"bypass_mode": "always"', code_gate)

    def test_r1_r5_production_guards_are_documented_as_defense_in_depth(self) -> None:
        poller = PULL_DEPLOY.read_text(encoding="utf-8")
        for marker in (
            'row.get("head_sha") == sha',
            'row.get("conclusion") == "success"',
            'row.get("name") == "validate"',
            "WAIT_CONTROL_PLANE_APPROVAL",
        ):
            self.assertIn(marker, poller)

        readiness = PUBLISH_READINESS.read_text(encoding="utf-8")
        self.assertIn("production HEAD does not equal current origin/main", readiness)
        self.assertIn("READINESS_STATE_NOT_CURRENT", readiness)

        text = normalized(DOC)
        self.assertIn("separate production-readiness boundary", text)
        self.assertIn(
            "do not turn the raw repository credential into a generated-path-only capability",
            text,
        )

    def test_migration_contract_forbids_broad_privileged_capability(self) -> None:
        text = normalized(DOC)
        for marker in (
            "not raw key readability",
            "not arbitrary Git/SSH execution",
            "expected base SHA",
            "expected publication commit SHA",
            "re-read remote `main` immediately before the network write",
            "explicit non-forced push",
            "only then remove/rotate/revoke",
        ):
            self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
