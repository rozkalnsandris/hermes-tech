# 2026-08-08 digest recovery follow-up

The first recovery run after PR #48 proved that the foreign-classifier-ID fix works: all 154 candidates were classified and no foreign batch ID was accepted. Two independent fail-closed conditions then stopped publication.

1. The Agents model response was syntactically valid JSON but contained only four digest source links for the required five selected items. The existing source reconciler correctly rejected it, but this semantic shape error happened after the quality-repair loop, so the category had no bounded model retry for that case.
2. While the long recovery pipeline was running, PR #49 advanced `origin/main` from `1d4d8568...` to `6fbae041...`. Publication correctly refused to create a generated-content commit from a stale production base (`REMOTE_AHEAD`). This guard must remain fail-closed; recovery must first synchronize/approve the exact current main SHA instead of auto-merging, rebasing, or force-pushing.

The source-shape repair adds a bounded semantic retry before a digest response is accepted. The Git synchronization behavior is intentionally unchanged.
