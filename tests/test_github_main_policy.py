from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import configure_github_main_policy as policy  # noqa: E402


class RecordingApi:
    def __init__(self, responses: Mapping[tuple[str, str], Any] | None = None) -> None:
        self.responses = dict(responses or {})
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, path, payload))
        key = (method, path)
        if key not in self.responses:
            raise AssertionError(f"unexpected API request: {method} {path}")
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        return response


class MainPolicyPayloadTests(unittest.TestCase):
    def test_integrity_rules_have_no_bypass(self) -> None:
        payload = policy.integrity_ruleset_payload()
        self.assertEqual(payload["bypass_actors"], [])
        self.assertEqual(
            {rule["type"] for rule in payload["rules"]},
            {"deletion", "non_fast_forward", "required_linear_history"},
        )
        self.assertEqual(
            payload["conditions"]["ref_name"]["include"],
            ["refs/heads/main"],
        )

    def test_only_code_gate_has_deploy_key_bypass(self) -> None:
        payload = policy.code_gate_ruleset_payload("validate")
        self.assertEqual(
            payload["bypass_actors"],
            [
                {
                    "actor_id": None,
                    "actor_type": "DeployKey",
                    "bypass_mode": "always",
                }
            ],
        )
        rules = {rule["type"]: rule for rule in payload["rules"]}
        self.assertEqual(
            rules["pull_request"]["parameters"]["allowed_merge_methods"],
            ["squash"],
        )
        self.assertEqual(
            rules["required_status_checks"]["parameters"]["required_status_checks"],
            [{"context": "validate"}],
        )
        self.assertTrue(
            rules["required_status_checks"]["parameters"][
                "strict_required_status_checks_policy"
            ]
        )

    def test_repository_settings_disable_non_squash_merges(self) -> None:
        self.assertEqual(
            policy.repository_settings_payload(),
            {
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
                "allow_squash_merge": True,
                "delete_branch_on_merge": True,
            },
        )

    def test_rule_subset_comparison_is_order_independent(self) -> None:
        expected = policy.integrity_ruleset_payload()
        actual = {
            **expected,
            "id": 42,
            "rules": list(reversed(expected["rules"])),
            "created_at": "2026-08-05T00:00:00Z",
        }
        policy._require_subset(actual, expected, "ruleset")


class MainPolicyPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = policy.RepositoryRef("rozkalnsandris", "hermes-tech")

    def test_single_expected_write_deploy_key_is_required(self) -> None:
        api = RecordingApi(
            {
                (
                    "GET",
                    "/repos/rozkalnsandris/hermes-tech/keys?per_page=100",
                ): [
                    {"id": 11, "title": "Hermes publisher", "read_only": False},
                    {"id": 12, "title": "Read-only audit", "read_only": True},
                ]
            }
        )
        key = policy.require_single_write_deploy_key(
            api,
            self.repository,
            "Hermes publisher",
        )
        self.assertEqual(key["id"], 11)

    def test_multiple_write_deploy_keys_fail_closed(self) -> None:
        api = RecordingApi(
            {
                (
                    "GET",
                    "/repos/rozkalnsandris/hermes-tech/keys?per_page=100",
                ): [
                    {"id": 11, "title": "Hermes publisher", "read_only": False},
                    {"id": 12, "title": "Legacy writer", "read_only": False},
                ]
            }
        )
        with self.assertRaisesRegex(policy.PolicyError, "exactly one write-enabled"):
            policy.require_single_write_deploy_key(
                api,
                self.repository,
                "Hermes publisher",
            )

    def test_successful_check_must_exist_on_exact_sha(self) -> None:
        sha = "a" * 40
        path = (
            "/repos/rozkalnsandris/hermes-tech/commits/"
            f"{sha}/check-runs?per_page=100"
        )
        api = RecordingApi(
            {
                ("GET", path): {
                    "check_runs": [
                        {"name": "validate", "conclusion": "success"},
                        {"name": "other", "conclusion": "failure"},
                    ]
                }
            }
        )
        policy.require_successful_check(api, self.repository, sha, "validate")

    def test_apply_confirmation_is_checked_before_api_access(self) -> None:
        api = RecordingApi()
        with self.assertRaisesRegex(policy.PolicyError, "confirmation mismatch"):
            policy.apply_policy(
                api,
                self.repository,
                "b" * 40,
                "Hermes publisher",
                "validate",
                "APPLY wrong/repo@" + "b" * 40,
            )
        self.assertEqual(api.calls, [])

    def test_classic_protection_404_is_accepted(self) -> None:
        path = "/repos/rozkalnsandris/hermes-tech/branches/main/protection"
        api = RecordingApi(
            {
                ("GET", path): policy.ApiError(404, "GET", path, "Not Found")
            }
        )
        policy.require_no_classic_branch_protection(api, self.repository)

    def test_existing_classic_protection_fails_closed(self) -> None:
        path = "/repos/rozkalnsandris/hermes-tech/branches/main/protection"
        api = RecordingApi({("GET", path): {"required_status_checks": {}}})
        with self.assertRaisesRegex(policy.PolicyError, "classic main branch protection"):
            policy.require_no_classic_branch_protection(api, self.repository)


if __name__ == "__main__":
    unittest.main()
