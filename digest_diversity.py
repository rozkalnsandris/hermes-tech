#!/usr/bin/env python3
"""Deterministic topic and vendor diversity contracts for Hermes Tech."""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Callable

LogFn = Callable[[str], None]

_VENDOR_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("google", ("google", "gcp", "gemini")),
    ("aws", ("aws", "amazon")),
    ("microsoft", ("microsoft", "azure", "openai")),
    ("meta", ("meta", "llama")),
    ("anthropic", ("anthropic", "claude")),
    ("moonshot", ("moonshot", "kimi")),
    ("deepseek", ("deepseek",)),
    ("docker", ("docker",)),
    ("kubernetes", ("kubernetes", "k8s")),
    ("cloudflare", ("cloudflare",)),
    ("huggingface", ("huggingface", "hugging face", "hf")),
)
_INSTALL_SENTINEL = "_HERMES_DIVERSITY_CONTRACTS_V2"


def _normalise_words(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def _topic_key(article: dict) -> str:
    value = article.get("topic_key")
    return value.strip() if isinstance(value, str) else ""


def _content_length(article: dict) -> int:
    value = article.get("content_length", 0)
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _article_label(article: dict, index: int) -> str:
    article_id = article.get("id")
    if isinstance(article_id, int) and not isinstance(article_id, bool):
        return str(article_id)
    return f"index:{index}"


def _vendor_key(article: dict) -> str:
    combined = _normalise_words(
        f"{Article.get('title', '')} {article.get('source', '')}"
    )
    padded = f" {combined} "
    for vendor, keywords in _VENDOR_PATTERNS:
        for keyword in keywords:
            normalised_keyword = _normalise_words(keyword)
            if normalised_keyword and f" {normalised_keyword} " in padded:
                return vendor

    source = _normalise_words(article.get("source", ""))
    return f"source:{source}" if source else "other"


def _format_counts(counts: Counter[str]) -> str:
    if not counts:
        return "none"
    return ",".join(f"{key}={counts[key]}" for key in sorted(counts))


def diversity_filter(
    articles: list[dict],
    max_count: int = 15,
    *,
    logger: LogFn | None = None,
) -> list[dict]:
    """Return a deterministic, topic-unique and vendor-diverse candidate list.

    Hard rule: every retained article has a non-empty, unique ``topic_key``.
    Representative rule: longest content wins within a topic; equal lengths keep
    the earliest input article. When truncation is needed, a greedy vendor count
    penalty prefers the least represented vendor and preserves original order for
    equal penalties.
    """
    emit = logger or (lambda _message: None)
    if isinstance(max_count, bool) or not isinstance(max_count, int) or max_count < 1:
        raise ValueError("max_count must be a positive integer")
    if not articles:
        emit("Diversity filter: input=0 topic_unique=0 selected=0")
        return []

    grouped: dict[str, list[tuple[int, dict]]] = {}
    invalid: list[str] = []
    for index, article in enumerate(articles):
        if not isinstance(article, dict):
            invalid.append(f"index:{index}")
            continue
        topic_key = _topic_key(article)
        if not topic_key:
            invalid.append(_article_label(article, index))
            continue
        grouped.setdefault(topic_key, []).append((index, article))

    if invalid:
        emit(
            "Diversity filter fail-closed: empty/invalid topic_key for "
            + ",".join(invalid)
        )
        return []

    records: list[dict[str, Any]] = []
    topic_dropped = 0
    for topic_key, group in grouped.items():
        first_index = group[0][0]
        representative_index, representative = min(
            group,
            key=lambda item: (-_content_length(item[1]), item[0]),
        )
        removed = [
            _article_label(article, index)
            for index, article in group
            if index != representative_index
        ]
        topic_dropped += len(removed)
        if removed:
            emit(
                f"Diversity topic dedup: topic={topic_key} "
                f"kept={_article_label(representative, representative_index)} "
                f"removed={','.join(removed)}"
            )
        records.append(
            {
                "first_index": first_index,
                "article": representative,
                "vendor": _vendor_key(representative),
            }
        )

    records.sort(key=lambda record: record["first_index"])
    if len(records) <= max_count:
        result = [record["article"] for record in records]
        vendor_counts = Counter(record["vendor"] for record in records)
        emit(
            f"Diversity filter: input={len(articles)} "
            f"topic_unique={len(records)} selected={len(result)} "
            f"topic_dropped={topic_dropped} cap_dropped=0 "
            f"vendors={_format_counts(vendor_counts)}"
        )
        return result

    remaining = list(records)
    selected: list[dict[str, Any]] = []
    selected_vendor_counts: Counter[str] = Counter()
    while remaining and len(selected) < max_count:
        best_position = min(
            range(len(remaining)),
            key=lambda position: (
                selected_vendor_counts[remaining[position]["vendor"]],
                remaining[position]["first_index"],
            ),
        )
        record = remaining.pop(best_position)
        selected.append(record)
        selected_vendor_counts[record["vendor"]] += 1

    capped_labels = [
        _article_label(record["article"], record["first_index"])
        for record in remaining
    ]
    if capped_labels:
        emit("Diversity cap removed: " + ",".join(capped_labels))

    result = [record["article"] for record in selected]
    emit(
        f"Diversity filter: input={len(articles)} "
        f"topic_unique={len(records)} selected={len(result)} "
        f"topic_dropped={topic_dropped} cap_dropped={len(remaining)} "
        f"vendors={_format_counts(selected_vendor_counts)}"
    )
    return result


def validate_selected_topic_keys(
    selected_ids: list[int],
    articles: list[dict],
    category: str,
) -> None:
    """Fail closed unless final selected IDs have distinct non-empty topics."""
    by_id = {article.get("id"): article for article in articles}
    seen: dict[str, int] = {}
    for article_id in selected_ids:
        article = by_id.get(article_id)
        if article is None:
            raise RuntimeError(
                f"[{category}] selected article ID {article_id} nav kandidātu kopā; "
                "fail-closed"
            )
        topic_key = _topic_key(article)
        if not topic_key:
            raise RuntimeError(
                f"[{category}] selected article ID {article_id} trūkst topic_key; "
                "fail-closed"
            )
        previous_id = seen.get(topic_key)
        if previous_id is not None:
            raise RuntimeError(
                f"[{category}] selected_ids pārkāpj max 1 per topic_key: "
                f"topic={topic_key} article_ids={previous_id},{article_id}; "
                "fail-closed"
            )
        seen[topic_key] = article_id


def install_diversity_contracts(core: Any) -> None:
    """Install issue #5 contracts into the legacy digest core, idempotently."""
    if getattr(core, _INSTALL_SENTINEL, False):
        return

    original_resolver = core._resolve_digest_selected_ids

    def installed_diversity_filter(
        articles: list[dict], max_count: int = 15
    ) -> list[dict]:
        return diversity_filter(articles, max_count=max_count, logger=core.log)

    def installed_selected_id_resolver(
        markdown: str,
        model_selected: Any,
        articles: list[dict],
        category: str,
    ) -> list[int]:
        selected_ids = original_resolver(
            markdown,
            model_selected,
            articles,
            category,
        )
        validate_selected_topic_keys(selected_ids, articles, category)
        return selected_ids

    installed_diversity_filter.__name__ = "diversity_filter"
    installed_selected_id_resolver.__name__ = "_resolve_digest_selected_ids"
    core.diversity_filter = installed_diversity_filter
    core._resolve_digest_selected_ids = installed_selected_id_resolver
    setattr(core, _INSTALL_SENTINEL, True)
