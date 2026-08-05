#!/usr/bin/env python3
"""Render legacy Hermes shell runners with exact timezone-contract patches."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PUBLISH_REPLACEMENTS = (
    (
        'DATE="${2:-$(date -u +%Y-%m-%d)}"',
        'DATE="${2:-$("$PYTHON" "$HERMES_TIME_PY" business-date)}"',
    ),
    (
        '    echo "date: ${DATE}T07:00:00+02:00"',
        '    echo "date: $("$PYTHON" "$HERMES_TIME_PY" publication-timestamp "$DATE")"',
    ),
)

DIGEST_RUNNER_REPLACEMENTS = (
    (
        'TODAY=$(TZ=UTC date +%Y-%m-%d)',
        'TODAY=$("$PYTHON" "$HERMES_TIME_PY" business-date)',
    ),
)


def render(kind: str, source: str) -> str:
    replacements = {
        "publish": PUBLISH_REPLACEMENTS,
        "digest-runner": DIGEST_RUNNER_REPLACEMENTS,
    }[kind]
    result = source
    for old, new in replacements:
        count = result.count(old)
        if count != 1:
            raise RuntimeError(
                f"{kind}: sagaidīta tieši viena timezone vieta, "
                f"atrastas {count}: {old!r}"
            )
        result = result.replace(old, new)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("publish", "digest-runner"))
    parser.add_argument("source", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        source = args.source.read_text(encoding="utf-8")
        rendered = render(args.kind, source)
    except (OSError, RuntimeError) as exc:
        print(f"KĻŪDA: timezone shell adapteris: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
