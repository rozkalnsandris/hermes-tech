# Digest core compatibility boundary

`digest.py` is the supported Hermes Tech digest runtime entrypoint.
`digest_core.py` is a legacy compatibility implementation container that is
patched with versioned runtime contracts before its API is exported.

## Diversity source of truth

`digest_diversity.py` is the only authoritative production implementation for:

- candidate topic deduplication;
- deterministic vendor balancing;
- the `max_count` candidate cap;
- final selected-topic uniqueness validation.

A historical `diversity_filter()` implementation remains inside
`digest_core.py` for compatibility with the legacy core file, but it is **not a
supported runtime selector**. It must not be called by production code.

`digest.py::_install_diversity_contracts()` installs the authoritative
implementation before `_export_core_api()` runs and then verifies that both
`diversity_filter` and `_resolve_digest_selected_ids` are owned by the
`digest_diversity` module. If that ownership check fails, startup fails closed
instead of falling back to the legacy algorithm.

## Import rule

Production Python modules in this repository must not import `digest_core`
directly. `digest.py` is the single allowed compatibility import and performs
all installers before exposing the core API. Tests may import `digest_core`
directly only when they are intentionally testing the legacy/core boundary.

This rule is executable in `tests/test_diversity_quarantine.py` so a future
refactor cannot silently add a second production path around the installer.

## Cleanup direction

The long-term direction is to continue extracting bounded contracts from the
legacy core until `digest_core.py` can be reduced or retired. Removing the
entire core in one change is intentionally avoided because it would mix a large
structural refactor with production selection behavior.

This quarantine does not change candidate order, diversity scoring, model calls,
database state or publication semantics.

`HERMES_TECH_DEPLOY_REQUIRED=yes`

`RPI5_MAIN_CHANGE_REQUIRED=no`
