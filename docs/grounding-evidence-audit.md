# Hermes Tech factual-grounding evidence audit

Hermes collector can retain more feed-provided text than the digest-generation
prompt currently receives. `collector_core.py::entry_texts()` stores sanitized
feed `entry.content` when available (otherwise the feed summary), while
`fetch_routed_candidates()` currently gives digest generation only the first 300
characters of the stored summary plus metadata and `content_length`.

This is a **measurement and decision gate**, not authorization to send more text
to the model.

## Read-only production audit

Run against the Hermes runtime root:

```bash
python tools/audit_grounding_evidence.py \
  --root "$HOME/hermes-tech" \
  --days 30 \
  --max-digests 90 \
  --evidence /tmp/hermes-grounding-audit.json
```

The tool parses `selected_ids` from recent source digests and measures the
corresponding rows in `data/hermes.db`. It reports only IDs/counts/source labels,
character lengths, aggregate evidence-depth statistics and the database hash.
It does **not** output article summary/content text, article URLs, `.env`, API
keys, prompts or model responses, and it performs no model/network call.

The SQLite database is opened read-only/query-only and its SHA-256 + size must be
unchanged when the audit finishes.

## What to measure before changing production prompts

The first real audit must establish at least:

- how many recent selected articles still have feed-provided content beyond the
  current 300-character prompt boundary;
- median, p90 and maximum stored content length;
- how many selected rows have more than 1,200 characters available;
- the aggregate additional character/token budget of a bounded 1,200-character
  candidate excerpt;
- missing selected IDs or selected rows without `digest_date`, which are data
  integrity problems and must be resolved before changing evidence behavior;
- whether the gain is concentrated in only a few feed sources.

The 1,200-character value is only a **review candidate** used to quantify the
trade-off. It is not a production setting.

## Provider cost and privacy boundary

DeepSeek API billing depends on input/output tokens and provider pricing can
change. The official API documentation should be checked again immediately
before a production evidence-budget change rather than copying a permanent
price into the repository.

DeepSeek's current API documentation also states that context caching on disk is
enabled by default. Therefore sending more feed content increases not only
model input size but also the amount of third-party source text processed by the
provider/cache path. A production change needs an explicit review of applicable
provider terms/data handling and the expected token-cost delta from the real
audit.

Do not send visitor data, local logs, secrets, `.env`, or private host data to
the model. This issue concerns only already-collected third-party feed evidence.

## Deliberate non-goals for this phase

This phase does **not**:

- fetch canonical article webpages;
- broaden the #79 RSS transport allowlist or redirect/size policy;
- change SQLite schema;
- change `fetch_routed_candidates()` output;
- change classifier/digest prompts;
- change model/token/output limits;
- add a second LLM summarization/compression pass;
- assign larger evidence budgets based on an unverified "official source" label;
- alter publication or review behavior.

Deterministic truncation of already-sanitized stored feed content is preferable
to an additional model compression layer if a later PR proves a larger evidence
budget is justified. Any production excerpt must remain inside the #80
untrusted-data delimiter/instruction boundary.

## Decision after the real audit

A later production PR may be proposed only after the real audit evidence is
reviewed. It should state the selected character/token budget, measured cost
impact, provider-data handling conclusion, adversarial fixtures and rollback
plan. If most selected feeds do not contain meaningful evidence beyond 300
characters, keeping the current smaller payload is a valid outcome.

Article-page fetching is a separate security/privacy/network architecture change
and is not implied by this audit.

`HERMES_TECH_DEPLOY_REQUIRED=no` for this read-only audit phase.

`RPI5_MAIN_CHANGE_REQUIRED=no`
