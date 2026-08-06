# Hermes Tech timezone contract

Hermes Tech uses two explicit time domains. They must not be inferred from the
host process timezone.

## UTC storage and source timestamps

RSS `published_parsed` and `updated_parsed` tuples from `feedparser` are treated
as UTC tuples. `hermes_time.rss_struct_time_to_utc_iso()` converts them with
`calendar.timegm()`. It never uses `time.mktime()`, so changing `TZ` on the host
cannot shift a source timestamp by the Berlin UTC offset.

Operational database timestamps such as `fetched_at`, `routed_at`, and manifest
`generated_at` remain aware UTC timestamps.

## Digest business date

The only digest/publication business timezone is:

```text
Europe/Berlin
```

`hermes_time.business_date()` projects the current UTC instant into that IANA
zone. Classification manifests, category digest filenames, validation, pipeline
publication, links, and the Telegram summary therefore use the same Berlin
calendar date. A run at `00:30` Berlin while UTC is still on the previous date
uses the new Berlin date.

The stable collector and digest entrypoints install this contract into the
byte-preserved legacy cores. UTC audit timestamps keep their original instant;
only the exact `%Y-%m-%d` business-date projection changes.

## Hugo publication timestamp

A digest is published at the logical local time `07:00:00` on its supplied
business date. `hermes_time.publication_timestamp()` attaches the
`Europe/Berlin` zone and emits RFC3339:

- winter: `2026-01-15T07:00:00+01:00`;
- summer: `2026-07-15T07:00:00+02:00`;
- DST start, 2026-03-29: `+02:00`;
- DST end, 2026-10-25: `+01:00`.

The supplied publication date is authoritative for manual recovery. Existing
published Markdown is not rewritten by this change.

## Shell compatibility boundary

`publish_core.sh` and `run_digests_core.sh` preserve the previously audited
operational bodies byte-for-byte. Their stable public entrypoints render exactly
two publication substitutions and one pipeline-date substitution through
`tools/timezone_shell_adapter.py`. Rendering fails closed unless every expected
legacy expression occurs exactly once.

Production resolves the core, adapter, and time module from
`HERMES_TECH_ROOT`. The tracked-checkout fallback exists only for isolated CI
fixtures that intentionally copy a public entrypoint without its sibling files.

## Commands

```bash
python hermes_time.py business-date
python hermes_time.py business-date --at 2026-08-05T22:30:00Z
python hermes_time.py publication-timestamp 2026-01-15
```

## Regression coverage

The tests cover:

- RSS tuples under UTC, fixed CET, and CET/CEST process settings;
- local-after-midnight while UTC is on the previous date;
- winter, summer, DST-start, and DST-end front-matter offsets;
- preservation of UTC audit timestamp values;
- exact shell rendering and fail-closed source-drift behavior;
- public entrypoint integration with isolated runtime roots.
