from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "publisher-credential-boundary.md"
SYNC = ROOT / "sync_generated_content.sh"
POLICY = ROOT / "tools" / "configure_github_main_policy.py"
POLLER = ROOT / "tools" / "pull-deploy" / "release" / "hermes-tech-pull-deploy"


def normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


class PublisherCredentialBoundaryTests(unittest.TestCase):
    def test_document_records_residual_risk_and_infrastructure_owner(self) -> None:
        text = normalized(DOC)
        for marker in (
            "least-privilege design gap, not an incident finding",
            "same-UID arbitrary code execution",
            "not forced through `sync_generated_content.sh`",
            "not a complete repository-write containment mechanism",
            "`rozkalnsandris/RPi5_main#93`",
            "dedicated non-login publisher identity",
            "HERMES_TECH_DEPLOY_REQUIRED=no",
            "RPI5_MAIN_CHANGE_REQUIRED=yes",
        ):
            self.assertIn(marker, text)

    def test_normal_publisher_still_has_exact_generated_path_and_sha_guards(self) -> None:
        source = SYNC.read_text(encoding="utf-8")
        for marker in (
            "readonly -a COMMIT_PATHS=",
            '[[ "$parent_sha" == "$expected_base" ]]',
            '[[ "$commit_subject" == "Publish $category digest $digest_date" ]]',
            "is_commit_path \"$path\"",
            '"$expected_commit:refs/heads/$BRANCH"',
            '[[ "$remote_sha" == "$expected_commit" ]]',
            '[[ "$local_sha" == "$remote_sha" ]]',
        ):
            self.assertIn(marker, source)

    def test_integrity_rules_have_no_bypass_but_code_gate_deploy_key_does(self) -> None:
        source = POLICY.read_text(encoding="utf-8")
        integrity_start = source.index("def integrity_ruleset_payload")
        code_start = source.index("def code_gate_ruleset_payload")
        repo_settings_start = source.index("def repository_settings_payload")
        integrity = source[integrity_start:code_start]
        code_gate = source[code_start:repo_settings_start]

        self.assertIn('"bypass_actors": []', integrity)
        self.assertIn('"actor_type": "DeployKey"', code_gate)
        self.assertIn('"bypass_mode": "always"', code_gate)

    def test_pull_deploy_defense_is_documented_without_overclaiming(self) -> None:
        poller = POLLER.read_text(encoding="utf-8")
        for marker in (
            'row.get("head_sha") == sha',
            'row.get("conclusion") == "success"',
            'row.get("name") == "validate"',
            "WAIT_CONTROL_PLANE_APPROVAL",
        ):
            self.assertIn(marker, poller)

        text = normalized(DOC)
        self.assertIn("useful defense in depth", text)
        self.assertIn(
            "post-push CI must not be described as equivalent to a pre-push server-side path restriction",
            text,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
