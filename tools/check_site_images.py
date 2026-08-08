#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

IMAGE_EXTENSIONS = {".avif", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
RASTER_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
HTML_IMG_RE = re.compile(r"<img\b", re.IGNORECASE)


class ImageTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "img":
            return
        values = {name.casefold(): value for name, value in attrs}
        self.images.append(
            {
                "src": values.get("src"),
                "alt": values.get("alt"),
                "width": values.get("width"),
                "height": values.get("height"),
            }
        )


def tracked_paths(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "site"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return sorted(
        raw.decode("utf-8")
        for raw in proc.stdout.split(b"\0")
        if raw
    )


def image_class(path: str) -> str:
    if path.startswith("site/content/"):
        return "content"
    if path.startswith("site/assets/"):
        return "assets"
    if path.startswith("site/static/og/"):
        return "static_og"
    if path.startswith("site/static/brand/"):
        return "static_brand"
    if path.startswith("site/static/"):
        return "static_other"
    return "other"


def positive_integer(value: str | None) -> bool:
    if value is None or not value.isdigit():
        return False
    return int(value) > 0


def is_local_source(src: str | None) -> bool:
    if not src or src.startswith("data:") or src.startswith("//"):
        return False
    parsed = urlsplit(src)
    return not parsed.scheme and not parsed.netloc


def content_image_usage(root: Path, paths: list[str]) -> dict[str, Any]:
    markdown_destinations: list[dict[str, str]] = []
    inline_img_files: list[str] = []
    for relative in paths:
        if not relative.startswith("site/content/") or not relative.endswith(".md"):
            continue
        text = (root / relative).read_text(encoding="utf-8")
        for destination in MARKDOWN_IMAGE_RE.findall(text):
            markdown_destinations.append({"file": relative, "destination": destination})
        if HTML_IMG_RE.search(text):
            inline_img_files.append(relative)
    return {
        "markdown_images": markdown_destinations,
        "markdown_image_count": len(markdown_destinations),
        "inline_img_files": sorted(inline_img_files),
        "inline_img_file_count": len(inline_img_files),
    }


def rendered_image_usage(public_dir: Path) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    for page in sorted(public_dir.rglob("*.html")):
        parser = ImageTagParser()
        parser.feed(page.read_text(encoding="utf-8"))
        parser.close()
        relative = page.relative_to(public_dir).as_posix()
        for image in parser.images:
            src = image["src"]
            local = is_local_source(src)
            width_ok = positive_integer(image["width"])
            height_ok = positive_integer(image["height"])
            images.append(
                {
                    "page": relative,
                    **image,
                    "local": local,
                    "has_alt": image["alt"] is not None,
                    "dimensions_ok": (not local) or (width_ok and height_ok),
                }
            )

    local_images = [image for image in images if image["local"]]
    missing_dimensions = [image for image in local_images if not image["dimensions_ok"]]
    missing_alt = [image for image in images if not image["has_alt"]]
    sources = Counter(str(image["src"]) for image in images)
    return {
        "img_tags": len(images),
        "local_img_tags": len(local_images),
        "unique_sources": len(sources),
        "sources": [
            {"src": source, "uses": uses}
            for source, uses in sorted(sources.items())
        ],
        "missing_dimensions": missing_dimensions,
        "missing_dimensions_count": len(missing_dimensions),
        "missing_alt": missing_alt,
        "missing_alt_count": len(missing_alt),
    }


def build_report(root: Path, public_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    public_dir = public_dir.resolve()
    paths = tracked_paths(root)
    images = [
        path
        for path in paths
        if Path(path).suffix.casefold() in IMAGE_EXTENSIONS
    ]
    classes = Counter(image_class(path) for path in images)
    extensions = Counter(Path(path).suffix.casefold() for path in images)
    raster_images = [
        path for path in images if Path(path).suffix.casefold() in RASTER_EXTENSIONS
    ]
    content_raster = [path for path in raster_images if path.startswith("site/content/")]
    return {
        "tracked": {
            "image_files": len(images),
            "raster_files": len(raster_images),
            "content_raster_files": content_raster,
            "content_raster_count": len(content_raster),
            "classes": dict(sorted(classes.items())),
            "extensions": dict(sorted(extensions.items())),
        },
        "content_usage": content_image_usage(root, paths),
        "rendered": rendered_image_usage(public_dir),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory Hermes Tech image surfaces and rendered image safety.")
    parser.add_argument("public_dir", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--require-local-dimensions", action="store_true")
    parser.add_argument("--require-alt", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(args.root, args.public_dir)
    except (OSError, UnicodeError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"KĻŪDA: image contract: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    if args.require_local_dimensions and report["rendered"]["missing_dimensions_count"]:
        print("KĻŪDA: generated local images without usable width/height", file=sys.stderr)
        return 2
    if args.require_alt and report["rendered"]["missing_alt_count"]:
        print("KĻŪDA: generated images without alt attributes", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
