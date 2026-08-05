"""Regression suite wrapper with resolved issue #5 diversity coverage."""
from __future__ import annotations

import digest_contracts_legacy as _legacy
from digest_diversity import diversity_filter

DeepSeekJsonContractTests = _legacy.DeepSeekJsonContractTests
SelectedIdReconciliationTests = _legacy.SelectedIdReconciliationTests
SourceRestorationTests = _legacy.SourceRestorationTests
QualityAndRoutingValidatorTests = _legacy.QualityAndRoutingValidatorTests


class TopicAndDiversityTests(_legacy.TopicAndDiversityTests):
    def test_known_issue_5_duplicate_topic_is_removed_even_below_limit(self) -> None:
        articles = [
            {"id": 1, "topic_key": "same", "content_length": 10},
            {"id": 2, "topic_key": "same", "content_length": 20},
            {"id": 3, "topic_key": "other", "content_length": 5},
        ]
        result = diversity_filter(articles, max_count=15)
        self.assertEqual([item["id"] for item in result], [2, 3])
