#!/usr/bin/env python3
"""Validate Hermes Tech repository hygiene and editorial source-of-truth."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterable, Sequence

CANONICAL_EDITORIAL_FILES = (
    "editorial/VOICE.md",
    "editorial/WRITING.md",
    "editorial/REVIEW.md",
)
CANONICAL_EDITORIAL_NAMES = tuple(
    PurePosixPath(path).name for path in CANONICAL_EDITORIAL_FILES
)
ROOT_PERSONA_FILES = ("SOUL.md", "STYLE.md", "VALUES.md")
HISTORICAL_PERSONA_MARKER = (
    "Status: historical, non-canonical project reference."
)
AGENTS_NONCANONICAL_MARKER = (
    "Root `SOUL.md`, `STYLE.md`, and `VALUES.md` are historical, "
    "non-canonical project references."
)

REQUIRED_IGNORE_PATTERNS = (
    "/.local-backups/",
    "/.publish-work.*",
    "/site/.hugo_build.lock",
    "/site/resources/",
    "/.pytest_cache/",
    "/.mypy_cache/",
    "/.ruff_cache/",
    "/evidence/",
    "/*.evidence.json",
    "/*-evidence-*.tar.gz",
    "/*.bundle",
    "/*.bundle.*",
)

IGNORED_SENTINELS = (
    ".local-backups/test.txt",
    ".publish-work.test/work.txt",
    "site/.hugo_build.lock",
    "site/resources/_gen/images/test.png",
    ".pytest_cache/v/cache/nodeids",
    ".mypy_cache/3.11/cache.json",
    ".ruff_cache/0.14/cache",
    "evidence/run.json",
    "run.evidence.json",
    "hermes-tech-evidence-20990101.tar.gz",
    "repo.bundle",
    "repo.bundle.sha256",
)

ACTIVE_SENTINELS = (
    "digests/2099-01-01-devops.md",
    "site/content/digest/2099-01-01.md",
    "site/static/og/2099-01-01-devops.png",
    "editorial/VOICE.md",
    "site/static/brand/hermes-tech-mark-v15.png",
)

FORBIDDEN_TRACKED_EXACT = {
    "site/.hugo_build.lock",
}
FORBIDDEN_TRACKED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
FORBIDDEN_TRACKED_PREFIXES = (
    ".local-backups/",
    ".publish-work.",
    "site/resources/",
    "site/layouts.bak-",
)
GENERATED_SITE_OUTPUTS = {
    "index.xml",
    "sitemap.xml",
    "robots.txt",
}
ASSET_EXTENSIONS = (
    "png",
    "jpg",
    "jpeg",
    "svg",
    "ico",
    "webmanifest",
    "xml",
)
QUOTED_ASSET_RE = re.compile(
    r"""(?P<quote>["'])(?P<path>/?[A-Za-z0-9][A-Za-z0-9_./-]*\."""
    + r"(?:" + "|".join(ASSET_EXTENSIONS) + r"))(?P=quote)"
)
HTML_ASSET_RE = re.compile(
    r"""(?:src|href|content)\s*=\s*(?P<quote>[#'])"""
    + r"""(?P<path>/[A-Za-z0-9][A-Za-z0-9_./-]*\.(?:"""
    + "|".join(ASSET_EXTENSIONS)
    + r"""))(?P=quote)""",
    re.IGNORECASE,
)
MARKDOWN_ASSET_RE = re.compile(
    r"""!\[[^\]]*\]\((?P<path>/[^)\s]+\.(?:"""
    + "|".join(ASSET_EXTENSIONS)
    + r"""))(?:\s+[#'][^"']*["'])?\)""",
    re.IGNORECASE,
)
CSS_ASSET_RE = re.compile(
    r"""url\(\s*(?P<quote>["']?)(?P<path>/"""
    + r"""[A-Za-z0-9][A-Za-z0-9_./-]*\.(?:"""
    + "|".join(ASSET_EXTENSIONS)
    + r"""))(?P=quote)\s*\)"""
)
VERSIONED_ASSET_RE = re.compile(r"(?:^|[-_/])v\d+(?:[-_.]|$)", re.IGNORECASE)


class HygieneError(RuntimeError):
    """Raised when a repository hygiene contract is violated."""


def _run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def tracked_paths(root: Path) -> tuple[str, ...]:
    proc = _run_git(root, "ls-files", "-z")
    return tuple(
        item.decode("utf-8")
        for item in proc.stdout.split(b"\0")
        if item
    )


def is_ignored(root: Path, relative: str) -> bool:
    proc = _run_git(
        root,
        "check-ignore",
        "--no-index",
        "--quiet",
        "--",
        relative,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise HygieneError(
            f"git check-ignore failed for {relative!r}: "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.returncode == 0


def validate_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    if not path.is_file():
        raise HygieneError("missing .gitignore")
    entries = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = [item for item in REQUIRED_IGNORE_PATTERNS if item not in entries]
    if missing:
        raise HygieneError(f".gitignore missing required patterns: {missing}")

    not_ignored = [item for item in IGNORED_SENTINELS if not is_ignored(root, item)]
    if not_ignored:
        raise HygieneError(f"transient paths are not ignored: {not_ignored}")

    accidentally_ignored = [
        item for item in ACTIVE_SENTINELS if is_ignored(root, item)
    ]
    if accidentally_ignored:
        raise HygieneError(
            "intentional tracked publication/editorial paths are ignored: "
            f"{accidentally_ignored}"
        )


def validate_no_tracked_transients(paths: Iterable[str]) -> None:
    violations: list[str] = []
    for raw in paths:
        path = PurePosixPath(raw)
        parts = set(path.parts)
        if raw in FORBIDDEN_TRACKED_EXACT:
            violations.append(raw)
            continue
        if parts & FORBIDDEN_TRACKED_PARTS:
            violations.append(raw)
            continue
        if any(raw.startswith(prefix) for prefix in FORBIDDEN_TRACKED_PREFIXES):
            violations.append(raw)
            continue
        if path.name.endswith((".pyc", ".pyo", ".swp", "~")):
            violations.append(raw)
    if violations:
        raise HygieneError(
            "tracked transient/cache/backup paths remain: "
            f"{sorted(violations)}"
        )


def _loaded_editorial_names(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "load_editorial_context"
        ),
        None,
    )
    if function is None:
        raise HygieneError("digest_core.py has no load_editorial_context()")
    names: list[str] = []
    has_editorial_dir = False
    for node in ast.walk(function):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "editorial":
                has_editorial_dir = True
            if node.value.endswith(".md"):
                names.append(PurePosixPath(node.value).name)
    if not has_editorial_dir:
        raise HygieneError(
            "load_editorial_context() does not select the editorial directory"
        )
    return tuple(dict.fromkeys(names))


def validate_editorial_contract(root: Path) -> None:
    missing = [
        relative
        for relative in CANONICAL_EDITORIAL_FILES
        if not (root / relative).is_file()
    ]
    if missing:
        raise HygieneError(f"missing canonical editorial files: {missing}")

    digest_core = root / "digest_core.py"
    if not digest_core.is_file():
        raise HygieneError("missing digest_core.py")
    loaded = _loaded_editorial_names(
        digest_core.read_text(encoding="utf-8")
    )
    if loaded != CANONICAL_EDITORIAL_NAMES:
        raise HygieneError(
            "digest editorial loader drift: "
            f"expected={CANONICAL_EDITORIAL_NAMES}, actual={loaded}"
        )

    unmarked: list[str] = []
    for relative in ROOT_PERSONA_FILES:
        path = root / relative
        if not path.is_file():
            raise HygieneError(f"missing retained historical persona file: {relative}")
        if HISTORICAL_PERSONA_MARKER not in path.read_text(encoding="utf-8"):
            unmarked.append(relative)
    if unmarked:
        raise HygieneError(
            f"root persona files are not marked non-canonical: {unmarked}"
        )

    agents = root / "AGENTS.md"
    if not agents.is_file():
        raise HygieneError("missing AGENTS.md")
    agents_text = agents.read_text(encoding="utf-8")
    if AGENTS_NONCANONICAL_MARKER not in agents_text:
        raise HygieneError("AGENTS.md does not mark root persona files non-canonical")
    for relative in CANONICAL_EDITORIAL_FILES:
        if f"`{relative}`" not in agents_text:
            raise HygieneError(f"AGENTS.md does not name canonical file {relative}")


def _text_reference_sources(paths: Iterable[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for raw in paths:
        path = PurePosixPath(raw)
        if raw.startswith("site/layouts/"):
            selected.append(raw)
        elif raw.startswith("site/content/") and path.suffix.lower() in {
            ".md",
            ".html",
        }:
            selected.append(raw)
        elif raw.startswith("site/static/") and path.suffix.lower() in {
            ".xml",
            ".webmanifest",
            ".html",
            ".css",
            ".js",
            ".txt",
        }:
            selected.append(raw)
    return tuple(selected)


def _asset_candidates(text: str, relative: str) -> set[str]:
    if relative.startswith("site/content/"):
        candidates = {
            match.group("path")
            for match in HTML_ASSET_RE.finditer(text)
        }
        candidates.update(
            match.group("path")
            for match in MARKDOWN_ASSET_RE.finditer(text)
        )
    else:
        candidates = {
            match.group("path")
            for match in QUOTED_ASSET_RE.finditer(text)
        }
    candidates.update(
        match.group("path")
        for match in CSS_ASSET_RE.finditer(text)
    )
    return candidates


def validate_static_asset_references(
    root: Path,
    paths: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tracked = set(paths)
    referenced: set[str] = set()
    missing: set[str] = set()
    for relative in _text_reference_sources(tracked):
        source = root / relative
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for candidate in _asset_candidates(text, relative):
            normalized = candidate.lstrip("/")
            if normalized in GENERATED_SITE_OUTPUTS:
                continue
            target = f"site/static/{normalized}"
            referenced.add(target)
            if target not in tracked:
                missing.add(f"{relative} -> {target}")

    if missing:
        raise HygieneError(
            "active templates/manifests reference missing static assets: "
            f"{sorted(missing)}"
        )

    versioned = {
        raw
        for raw in tracked
        if raw.startswith("site/static/")
        and VERSIONED_ASSET_RE.search(PurePosixPath(raw).name)
    }
    unreferenced = tuple(sorted(versioned - referenced))
    return tuple(sorted(referenced)), unreferenced


def validate_repository(root: Path) -> dict[str, object]:
    if not (root / ".git").exists():
        raise HygieneError(f"not a Git worktree: {root}")
    paths = tracked_paths(root)
    validate_gitignore(root)
    validate_no_tracked_transients(paths)
    validate_editorial_contract(root)
    references, unreferenced = validate_static_asset_references(root, paths)
    return {
        "tracked_paths": len(paths),
        "referenced_static_assets": references,
        "unreferenced_versioned_assets_retained": unreferenced,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of tools/)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        report = validate_repository(root)
    except HygieneError as exc:
        print(f"KĻŪDA: repository hygiene check failed: {exc}", file=sys.stderr)
        return 1

    print(
        "Repository hygiene OK: "
        f"{report['tracked_paths']} tracked paths, "
        f"{len(report['referenced_static_assets'])} active static references, "
        f"{len(report['unreferenced_versioned_assets_retained'])} "
        "unreferenced versioned assets retained for visual review"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
