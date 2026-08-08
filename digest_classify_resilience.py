#!/usr/bin/env python3
"""Fail-closed semantic resilience for Hermes Tech global classification.

DeepSeek JSON mode guarantees JSON syntax, but application-domain identifiers still
need validation. This contract strengthens the allowed-ID prompt and retries only
responses that violate the batch identity boundary. Other RuntimeErrors remain
non-retryable and bubble to the normal pipeline failure path.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

FOREIGN_ARTICLE_ID_RE = re.compile(r"^article_id \d+ nav šī batch kandidātos$")
SEMANTIC_MAX_ATTEMPTS = 3
SEMANTIC_BACKOFF_SECONDS = (0, 1, 3)


def _is_foreign_article_id_error(exc: RuntimeError) -> bool:
    return FOREIGN_ARTICLE_ID_RE.fullmatch(str(exc).strip()) is not None


def _validate_best_source_ids(events: list[dict], allowed_ids: set[int]) -> None:
    for event in events:
        article_ids = set(event.get("article_ids") or [])
        best_source_ids = event.get("best_source_ids") or []
        if not isinstance(best_source_ids, list):
            raise RuntimeError("best_source_ids nav saraksts")
        for source_id in best_source_ids:
            if not isinstance(source_id, int):
                raise RuntimeError(
                    f"Nederīgs best_source_id tipa {type(source_id)}: {source_id}"
                )
            if source_id not in allowed_ids:
                raise RuntimeError(
                    f"article_id {source_id} nav šī batch kandidātos"
                )
            if source_id not in article_ids:
                raise RuntimeError(
                    f"best_source_id {source_id} nav sava notikuma article_ids"
                )


def install_classify_resilience_contracts(core: Any) -> None:
    """Install prompt + semantic retry guards on a digest_core-like module."""
    if getattr(core, "_hermes_classify_resilience_v1", False):
        return

    original_prompt: Callable[..., str] = core.build_classify_user_prompt
    original_classify: Callable[..., tuple[list[dict], list[int]]] = core.classify_batch

    def guarded_prompt(
        articles: list[dict], known_events: list[dict] | None = None
    ) -> str:
        prompt = original_prompt(articles, known_events)
        allowed_ids = [article["id"] for article in articles]
        return (
            f"{prompt}\n\n"
            "STRICT IDENTITY BOUNDARY:\n"
            f"The only allowed article IDs in this response are: {allowed_ids}.\n"
            "Every article_ids and best_source_ids value MUST be copied exactly "
            "from that list. Never invent, infer, renumber, or reuse an ID from "
            "another batch. best_source_ids MUST also be a subset of the same "
            "event's article_ids."
        )

    def guarded_classify(
        api_key: str,
        articles: list[dict],
        known_events: list[dict] | None = None,
    ) -> tuple[list[dict], list[int]]:
        allowed_ids = {article["id"] for article in articles}
        last_error: RuntimeError | None = None

        for attempt in range(1, SEMANTIC_MAX_ATTEMPTS + 1):
            if attempt > 1:
                delay = SEMANTIC_BACKOFF_SECONDS[attempt - 1]
                core.log(
                    "CLASSIFY SEMANTIC RETRY "
                    f"{attempt}/{SEMANTIC_MAX_ATTEMPTS} pēc {delay}s; "
                    "iepriekšējā atbilde pārkāpa batch article_id robežu"
                )
                if delay:
                    time.sleep(delay)
            try:
                events, missing = original_classify(
                    api_key, articles, known_events=known_events
                )
                _validate_best_source_ids(events, allowed_ids)
                return events, missing
            except RuntimeError as exc:
                if not _is_foreign_article_id_error(exc):
                    raise
                last_error = exc
                core.log(
                    "CLASSIFY INVALID ID: "
                    f"{exc}; atbilde noraidīta, nekas no tās netiek pieņemts"
                )
                if attempt >= SEMANTIC_MAX_ATTEMPTS:
                    raise

        assert last_error is not None
        raise last_error

    core.build_classify_user_prompt = guarded_prompt
    core.classify_batch = guarded_classify
    core._hermes_classify_resilience_v1 = True
