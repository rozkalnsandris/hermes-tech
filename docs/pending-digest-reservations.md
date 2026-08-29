# Pending digest reservations

Hermes Tech treats generated-but-unpublished digest files as reservations, not
as disposable scratch output.

## Why this exists

Publication can be blocked after digest generation. Until publication commits
the selected articles, those rows still have `digest_date IS NULL`. A later
digest run must therefore not assume that every such DB row is free: another
pending digest may already reference it.

The pending-draft contract prevents two forms of reuse:

- the same `article_id` in more than one pending digest;
- a different article with the same `(primary_category, topic_key)` in a later
  pending digest.

The second rule preserves event-level uniqueness even when the feed contains
multiple stories about the same event.

## Source of pending state

`digest_pending.py` derives pending drafts from the production Git working tree.
Only unstaged or untracked files matching these forms are accepted:

- `digests/YYYY-MM-DD-devops.md`
- `digests/YYYY-MM-DD-ai.md`
- `digests/YYYY-MM-DD-agents.md`
- legacy DevOps `digests/YYYY-MM-DD.md`

Staged digest changes fail closed. Each draft must be a regular non-symlink
file with an exact first-line `selected_ids` marker. The backlog is bounded to
31 distinct dates, matching the generated-content deployment/publication
backlog limit.

The DB is authoritative for each selected ID's `primary_category`,
`digest_date`, and `topic_key`. Missing IDs, category mismatches, an assignment
to a different digest date, or an empty topic key fail closed.

## Generation reservation

The grounded candidate loader still starts from routed rows where
`digest_date IS NULL`, but before diversity/model selection it removes any
candidate whose article ID or category/topic key is owned by another pending
draft.

A rerun of the same date/category may reuse its own current draft candidates.
If an older pending draft owns the same ID or topic, the older reservation
still wins and the candidate is excluded.

No reservation is written to SQLite and no schema change is required.

## Publication hard gate

`publish.sh` runs:

```bash
python digest_pending.py validate \
  --root "$HERMES_TECH_ROOT" \
  --db "$HERMES_TECH_ROOT/data/hermes.db"
```

before `publish_core.sh` can change Hugo content, live public files, SQLite,
the Git index, or `origin/main`.

The validator rejects cross-draft article-ID reuse and same-category
`topic_key` reuse across dates. This turns a multi-day backlog conflict into a
pre-mutation failure rather than allowing an older publication to make a later
draft invalid mid-batch.

## Recovery rule

Do not delete or silently edit older pending drafts to resolve a collision.
Preserve evidence and treat the oldest pending draft as the earlier reservation.
Regenerate or explicitly reconcile the later conflicting draft under the normal
generation/editorial contracts, then rerun the read-only pending preflight
before publication.
