#!/usr/bin/env bash
set -Eeuo pipefail

printf '%s\n' \
  'ERROR: this one-time 2026-08-07 recovery helper is retired.' \
  'The incident was completed under issue #33.' \
  'Use the guarded pull-deploy path documented in docs/public-repository-hardening.md.' >&2
exit 1
