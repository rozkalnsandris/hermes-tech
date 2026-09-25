import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".github" / "github-api-access-v1.json"
FAST_CONTRACT = ROOT / "docs" / "FAST_LANE_V2_2.md"
SHARED_REVISION = "3bb0740b5f0a8ce631d2ff79f1acc4999ff6ed2c"


class GitHubApiAccessContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.fast_contract = FAST_CONTRACT.read_text(encoding="utf-8")

    def test_manifest_pins_accepted_shared_contract(self):
        self.assertEqual(
            self.manifest["schema"],
            "rozkalns.github-api-access-consumer.v1",
        )
        self.assertEqual(self.manifest["repository"], "rozkalnsandris/hermes-tech")
        self.assertEqual(
            self.manifest["shared_contract"]["revision"],
            SHARED_REVISION,
        )
        self.assertEqual(self.manifest["tracking"]["rollout_issue"], 112)

    def test_reads_are_minimum_sufficient_serial_and_not_tight_polled(self):
        read_plan = self.manifest["read_plan"]
        self.assertTrue(read_plan["serial_by_default_per_repository_lane"])
        self.assertTrue(read_plan["minimum_sufficient_retrieval_required"])
        self.assertTrue(read_plan["changed_files_on_demand_only"])
        self.assertTrue(read_plan["event_or_state_driven_continuation_preferred"])
        self.assertFalse(read_plan["tight_polling_allowed"])
        self.assertFalse(read_plan["historical_workflow_runs_by_default"])

    def test_ambiguous_post_dispatch_outcomes_never_duplicate_mutation(self):
        expected = {
            "post_dispatch_429": "MUTATION_OUTCOME_UNKNOWN_RATE_LIMIT",
            "post_dispatch_timeout": "MUTATION_OUTCOME_UNKNOWN_TIMEOUT",
            "post_dispatch_transport_error": "MUTATION_OUTCOME_UNKNOWN_TRANSPORT",
        }
        synthetic = self.manifest["synthetic_acceptance"]
        self.assertFalse(synthetic["intentionally_exhaust_real_quota"])
        for case_name, disposition in expected.items():
            with self.subTest(case=case_name):
                case = synthetic[case_name]
                self.assertEqual(case["disposition"], disposition)
                self.assertFalse(case["automatic_duplicate_mutation_allowed"])
                self.assertTrue(case["requires_reconciliation"])
                self.assertTrue(case["stop"])

    def test_mutation_boundary_is_fail_closed_and_does_not_grant_live(self):
        boundary = self.manifest["mutation_boundary"]
        self.assertEqual(boundary["final_pre_mutation_budget_class"], "FINAL_PREMERGE_COMPACT")
        self.assertTrue(boundary["expected_head_binding_required_when_supported"])
        self.assertTrue(boundary["post_dispatch_uncertainty_fail_closed"])
        self.assertTrue(boundary["minimal_read_only_reconciliation_only"])
        self.assertTrue(boundary["stop_after_ambiguous_outcome"])
        self.assertFalse(boundary["merge_success_implies_live_or_deploy_authority"])

    def test_fast_startup_contract_binds_manifest_without_widening_authority(self):
        self.assertIn("`.github/github-api-access-v1.json`", self.fast_contract)
        self.assertIn("never tight-poll CI or reviews", self.fast_contract)
        self.assertIn("Never issue an automatic duplicate mutation", self.fast_contract)
        self.assertIn("does not create publish, deploy, runtime", self.fast_contract)
        local_rules = self.manifest["local_stricter_rules"]
        self.assertTrue(local_rules["fast_merge_requires_explicit_owner_decision"])
        self.assertTrue(local_rules["live_publish_deploy_runtime_requires_exact_authority"])
        self.assertTrue(local_rules["editorial_and_production_safety_rules_unchanged"])


if __name__ == "__main__":
    unittest.main()
