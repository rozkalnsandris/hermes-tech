#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any
from urllib.parse import unquote, urlsplit


SCHEMA_VERSION = 1


class StylesheetReference:
    def __init__(
        self,
        href: str,
        integrity: str | None,
        crossorigin: str | None,
    ) -> None:
        self.href = href
        self.integrity = integrity
        self.crossorigin = crossorigin


class _HTMLBaselineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stylesheets: list[StylesheetReference] = []
        self.script_tags = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered_tag = tag.casefold()
        if lowered_tag == "script":
            self.script_tags += 1
            return
        if lowered_tag != "link":
            return

        values = {
            name.casefold(): value
            for name, value in attrs
            if value is not None
        }
        rel = values.get("rel", "")
        rel_tokens = {token.casefold() for token in rel.split()}
        if "stylesheet" not in rel_tokens:
            return

        href = values.get("href")
        if not href:
            return
        self.stylesheets.append(
            StylesheetReference(
                href=href,
                integrity=values.get("integrity"),
                crossorigin=values.get("crossorigin"),
            )
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_files(public_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in public_dir.rglob("*")
            if path.is_file()
        ),
        key=lambda path: path.relative_to(public_dir).as_posix(),
    )


def _parse_html(path: Path) -> _HTMLBaselineParser:
    parser = _HTMLBaselineParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser


def _resolve_local_href(public_dir: Path, href: str) -> Path | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None

    raw_path = unquote(parsed.path)
    if not raw_path:
        return None

    relative = PurePosixPath(raw_path.lstrip("/"))
    if ".." in relative.parts:
        return None

    candidate = public_dir.joinpath(*relative.parts)
    try:
        candidate.resolve().relative_to(public_dir.resolve())
    except ValueError:
        return None
    return candidate


def capture_baseline(
    public_dir: Path,
    *,
    source_revision: str | None = None,
    hugo_version: str | None = None,
) -> dict[str, Any]:
    public_dir = public_dir.resolve()
    if not public_dir.is_dir():
        raise ValueError(f"generated public directory does not exist: {public_dir}")

    files = _relative_files(public_dir)
    manifest: list[dict[str, Any]] = []
    type_totals = {
        "html": {"files": 0, "bytes": 0},
        "css": {"files": 0, "bytes": 0},
        "xml": {"files": 0, "bytes": 0},
    }
    script_tags = 0

    for path in files:
        relative = path.relative_to(public_dir).as_posix()
        size = path.stat().st_size
        suffix = path.suffix.casefold()
        manifest.append(
            {
                "path": relative,
                "bytes": size,
                "sha256": _sha256(path),
            }
        )

        category = suffix.removeprefix(".")
        if category in type_totals:
            type_totals[category]["files"] += 1
            type_totals[category]["bytes"] += size

        if suffix == ".html":
            script_tags += _parse_html(path).script_tags

    homepage = public_dir / "index.html"
    homepage_stylesheets: list[dict[str, Any]] = []
    if homepage.is_file():
        parser = _parse_html(homepage)
        for stylesheet in parser.stylesheets:
            entry: dict[str, Any] = {
                "href": stylesheet.href,
                "integrity": stylesheet.integrity,
                "crossorigin": stylesheet.crossorigin,
            }
            local_path = _resolve_local_href(public_dir, stylesheet.href)
            if local_path is not None and local_path.is_file():
                entry.update(
                    {
                        "path": local_path.relative_to(public_dir).as_posix(),
                        "bytes": local_path.stat().st_size,
                        "sha256": _sha256(local_path),
                    }
                )
            homepage_stylesheets.append(entry)

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_revision": source_revision,
        "hugo_version": hugo_version,
        "generated": {
            "files": len(files),
            "bytes": sum(item["bytes"] for item in manifest),
            "html": type_totals["html"],
            "css": type_totals["css"],
            "xml": type_totals["xml"],
            "script_tags": script_tags,
        },
        "homepage_stylesheets": homepage_stylesheets,
        "manifest": manifest,
    }
    return result


def render_baseline(baseline: dict[str, Any]) -> str:
    return json.dumps(
        baseline,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture a deterministic baseline for a generated Hugo public tree."
    )
    parser.add_argument("public_dir", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--hugo-version")
    parser.add_argument(
        "--require-zero-scripts",
        action="store_true",
        help="Exit non-zero if any generated HTML contains a script tag.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline = capture_baseline(
            args.public_dir,
            source_revision=args.source_revision,
            hugo_version=args.hugo_version,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"KĻŪDA: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(render_baseline(baseline))
    if args.require_zero_scripts and baseline["generated"]["script_tags"] != 0:
        print(
            "KĻŪDA: generated site violates the JS-free contract: "
            f"{baseline['generated']['script_tags']} script tag(s)",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
