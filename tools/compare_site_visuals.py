#!/usr/bin/env python3
"""Capture baseline/current Hugo pages and require pixel-identical PNG output."""

from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = {
    "desktop": (1440, 1200),
    "mobile": (390, 844),
}
STATIC_PAGES = {
    "home": "/",
    "section": "/digest/",
    "how-it-works": "/how-hermes-works/",
    "impressum": "/impressum/",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def find_browser() -> str:
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise SystemExit("KĻŪDA: vizuālajai salīdzināšanai nav atrasts Chrome/Chromium")


def safe_extract_repository(base_sha: str, destination: Path) -> None:
    archive = destination.parent / "base.tar"
    run([
        "git",
        "archive",
        "--format=tar",
        f"--output={archive}",
        base_sha,
    ])

    with tarfile.open(archive, mode="r:") as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"KĻŪDA: nedrošs Git arhīva ceļš: {member.name!r}")
            if member.issym() or member.islnk() or member.isdev():
                raise SystemExit(f"KĻŪDA: neatļauts Git arhīva tips: {member.name!r}")
        source.extractall(destination, members=members)


def build_site(source_root: Path, destination: Path, cache: Path) -> None:
    destination.mkdir(parents=True)
    cache.mkdir(parents=True)
    environment = os.environ.copy()
    environment["HUGO_CACHEDIR"] = str(cache)
    subprocess.run(
        [
            "hugo",
            "--source",
            str(source_root / "site"),
            "--destination",
            str(destination),
            "--cleanDestinationDir",
            "--minify",
            "--noBuildLock",
        ],
        cwd=source_root,
        env=environment,
        check=True,
    )


def representative_pages(current_site: Path) -> dict[str, str]:
    pages = dict(STATIC_PAGES)
    article_pages = sorted((current_site / "digest").glob("*/index.html"))
    if not article_pages:
        raise SystemExit("KĻŪDA: Hugo būvē nav reprezentatīvas digest raksta lapas")
    relative = article_pages[0].relative_to(current_site).parent.as_posix()
    pages["article"] = f"/{relative}/"
    return pages


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def serve(directory: Path) -> Iterator[str]:
    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def capture(
    browser: str,
    base_url: str,
    page_path: str,
    viewport: tuple[int, int],
    output: Path,
    profile: Path,
) -> None:
    width, height = viewport
    output.parent.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)
    url = f"{base_url}{quote(page_path, safe='/')}"
    run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--no-sandbox",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            "--virtual-time-budget=2000",
            f"--user-data-dir={profile}",
            f"--screenshot={output}",
            url,
        ]
    )
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit(f"KĻŪDA: pārlūks neizveidoja ekrānattēlu: {output}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise SystemExit("KĻŪDA: vizuālo pierādījumu mape nedrīkst atrasties repozitorijā")

    browser = find_browser()
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hermes-tech-visual-") as temporary:
        work = Path(temporary)
        baseline_root = work / "baseline-source"
        baseline_root.mkdir()
        safe_extract_repository(args.base_sha, baseline_root)

        baseline_site = work / "baseline-public"
        current_site = work / "current-public"
        build_site(baseline_root, baseline_site, work / "baseline-cache")
        build_site(ROOT, current_site, work / "current-cache")

        pages = representative_pages(current_site)
        for label, page_path in pages.items():
            baseline_page = baseline_site / page_path.lstrip("/") / "index.html"
            current_page = current_site / page_path.lstrip("/") / "index.html"
            if page_path == "/":
                baseline_page = baseline_site / "index.html"
                current_page = current_site / "index.html"
            if not baseline_page.is_file() or not current_page.is_file():
                raise SystemExit(
                    f"KĻŪDA: trūkst salīdzināmās lapas {page_path}: "
                    f"baseline={baseline_page.is_file()} current={current_page.is_file()}"
                )

        with ExitStack() as stack:
            baseline_url = stack.enter_context(serve(baseline_site))
            current_url = stack.enter_context(serve(current_site))

            for viewport_name, viewport in VIEWPORTS.items():
                for page_name, page_path in pages.items():
                    baseline_png = output / "baseline" / viewport_name / f"{page_name}.png"
                    current_png = output / "current" / viewport_name / f"{page_name}.png"
                    capture(
                        browser,
                        baseline_url,
                        page_path,
                        viewport,
                        baseline_png,
                        work / "profiles" / f"baseline-{viewport_name}-{page_name}",
                    )
                    capture(
                        browser,
                        current_url,
                        page_path,
                        viewport,
                        current_png,
                        work / "profiles" / f"current-{viewport_name}-{page_name}",
                    )

                    baseline_hash = sha256(baseline_png)
                    current_hash = sha256(current_png)
                    print(
                        f"VISUAL {viewport_name} {page_name}: "
                        f"baseline={baseline_hash} current={current_hash}"
                    )
                    if baseline_png.read_bytes() != current_png.read_bytes():
                        raise SystemExit(
                            f"KĻŪDA: vizuālā atšķirība: {viewport_name}/{page_name}; "
                            f"captures={output}"
                        )

    print(
        "Site visual equivalence PASS: "
        f"{len(VIEWPORTS)} viewports × {len(pages)} pages, byte-identical PNGs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
