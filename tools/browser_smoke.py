#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


TAB_KEY = "\ue004"
PUBLIC_ORIGIN = "https://tech.rozkalns.net"


class RecordingHandler(SimpleHTTPRequestHandler):
    request_log: list[tuple[str, int]] = []

    def log_message(self, _format: str, *args: object) -> None:
        return

    def send_response(self, code: int, message: str | None = None) -> None:
        self.request_log.append((self.path, code))
        super().send_response(code, message)


class WebDriverError(RuntimeError):
    pass


class WebDriver:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.session_id: str | None = None

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 20,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.endpoint + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            detail = getattr(exc, "read", lambda: b"")()
            raise WebDriverError(
                f"WebDriver {method} {path} failed: {exc}; {detail.decode('utf-8', 'replace')}"
            ) from exc
        if not raw:
            return None
        decoded = json.loads(raw.decode("utf-8"))
        value = decoded.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise WebDriverError(f"WebDriver {method} {path}: {value}")
        return value

    def create_session(self, chrome_binary: str) -> None:
        value = self.request(
            "POST",
            "/session",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "chrome",
                        "goog:chromeOptions": {
                            "binary": chrome_binary,
                            "args": [
                                "--headless=new",
                                "--no-sandbox",
                                "--disable-dev-shm-usage",
                                "--disable-gpu",
                                "--hide-scrollbars",
                            ],
                        },
                        "goog:loggingPrefs": {"browser": "ALL"},
                    }
                }
            },
        )
        if not isinstance(value, dict):
            raise WebDriverError(f"unexpected new-session response: {value!r}")
        session_id = value.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise WebDriverError(f"missing sessionId: {value!r}")
        self.session_id = session_id

    def session_path(self, suffix: str) -> str:
        if not self.session_id:
            raise WebDriverError("WebDriver session not created")
        return f"/session/{self.session_id}{suffix}"

    def navigate(self, url: str) -> None:
        self.request("POST", self.session_path("/url"), {"url": url})

    def execute(self, script: str, args: list[Any] | None = None) -> Any:
        return self.request(
            "POST",
            self.session_path("/execute/sync"),
            {"script": script, "args": args or []},
        )

    def set_viewport(self, width: int, height: int) -> None:
        self.request(
            "POST",
            self.session_path("/window/rect"),
            {"x": 0, "y": 0, "width": width, "height": height},
        )

    def press_tab(self) -> None:
        self.request(
            "POST",
            self.session_path("/actions"),
            {
                "actions": [
                    {
                        "type": "key",
                        "id": "keyboard",
                        "actions": [
                            {"type": "keyDown", "value": TAB_KEY},
                            {"type": "keyUp", "value": TAB_KEY},
                        ],
                    }
                ]
            },
        )

    def browser_logs(self) -> list[dict[str, Any]]:
        value = self.request(
            "POST",
            self.session_path("/se/log"),
            {"type": "browser"},
        )
        return value if isinstance(value, list) else []

    def close(self) -> None:
        if self.session_id:
            with contextlib.suppress(Exception):
                self.request("DELETE", self.session_path(""))
            self.session_id = None


def executable(name: str, env_var: str | None = None) -> str:
    candidate = shutil.which(name)
    if candidate:
        return candidate
    if env_var:
        root = os.environ.get(env_var)
        if root:
            path = Path(root) / name
            if path.is_file():
                return str(path)
    raise RuntimeError(f"required browser executable not found: {name}")


def chrome_binary() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        candidate = shutil.which(name)
        if candidate:
            return candidate
    raise RuntimeError("Google Chrome/Chromium executable not found")


def wait_for_webdriver(endpoint: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint + "/status", timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("value", {}).get("ready"):
                return
        except (OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"ChromeDriver did not become ready: {last_error}")


def run_version(command: str, *args: str) -> str:
    return subprocess.run(
        [command, *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ).stdout.strip()


def build_site(root: Path, destination: Path, cache: Path) -> None:
    subprocess.run(
        [
            "hugo",
            "--source",
            str(root / "site"),
            "--destination",
            str(destination),
            "--cleanDestinationDir",
            "--minify",
            "--noBuildLock",
            "--logLevel",
            "info",
            "--panicOnWarning",
        ],
        cwd=root,
        env={**os.environ, "HUGO_CACHEDIR": str(cache)},
        check=True,
    )


def first_article_path(public: Path) -> str:
    candidates = sorted((public / "digest").glob("????-??-??/index.html"), reverse=True)
    if not candidates:
        raise RuntimeError("generated site has no representative digest article")
    return "/" + candidates[0].parent.relative_to(public).as_posix() + "/"


def local_href_to_path(public: Path, href: str) -> Path | None:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None
    raw = unquote(parsed.path)
    if not raw.startswith("/") or raw == "/":
        return public / "index.html"
    candidate = public / raw.lstrip("/")
    if raw.endswith("/"):
        candidate = candidate / "index.html"
    elif candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


def validate_internal_links(public: Path) -> None:
    from html.parser import HTMLParser

    class LinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.hrefs: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.casefold() != "a":
                return
            values = {name.casefold(): value for name, value in attrs}
            href = values.get("href")
            if href:
                self.hrefs.append(href)

    missing: list[tuple[str, str]] = []
    for page in sorted(public.rglob("*.html")):
        parser = LinkParser()
        parser.feed(page.read_text(encoding="utf-8"))
        parser.close()
        for href in parser.hrefs:
            if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue
            target = local_href_to_path(public, href)
            if target is not None and not target.exists():
                missing.append((page.relative_to(public).as_posix(), href))
    if missing:
        raise RuntimeError(f"broken internal links: {missing[:20]!r}")


def page_state(driver: WebDriver) -> dict[str, Any]:
    value = driver.execute(
        """
        const root = document.documentElement;
        const links = [...document.querySelectorAll('a')];
        const images = [...document.images];
        const canonical = document.querySelector('link[rel="canonical"]');
        const nav = document.querySelector('nav.site-nav');
        return {
          ready: document.readyState,
          title: document.title,
          path: location.pathname,
          canonical: canonical ? canonical.href : null,
          scripts: document.querySelectorAll('script').length,
          stylesheets: document.styleSheets.length,
          h1s: document.querySelectorAll('h1').length,
          headers: document.querySelectorAll('header.site').length,
          navs: document.querySelectorAll('nav.site-nav').length,
          mains: document.querySelectorAll('main.wrap').length,
          footers: document.querySelectorAll('footer.site').length,
          emptyLinks: links.filter(a => !((a.innerText || a.getAttribute('aria-label') || '').trim())).length,
          unloadedImages: images.filter(img => !img.complete || img.naturalWidth <= 0).map(img => img.getAttribute('src')),
          overflow: root.scrollWidth > root.clientWidth + 1,
          navDisplay: nav ? getComputedStyle(nav).display : null,
          navColumns: nav ? getComputedStyle(nav).gridTemplateColumns : null,
        };
        """
    )
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected page-state value: {value!r}")
    return value


def assert_page_state(state: dict[str, Any], expected_path: str) -> None:
    failures: list[str] = []
    expected_canonical = PUBLIC_ORIGIN + expected_path
    checks = {
        "ready": state.get("ready") == "complete",
        "path": state.get("path") == expected_path,
        "canonical": state.get("canonical") == expected_canonical,
        "scripts": state.get("scripts") == 0,
        "stylesheets": state.get("stylesheets") == 1,
        "h1s": state.get("h1s") == 1,
        "headers": state.get("headers") == 1,
        "navs": state.get("navs") == 1,
        "mains": state.get("mains") == 1,
        "footers": state.get("footers") == 1,
        "emptyLinks": state.get("emptyLinks") == 0,
        "images": state.get("unloadedImages") == [],
        "overflow": state.get("overflow") is False,
    }
    for name, ok in checks.items():
        if not ok:
            failures.append(f"{name}={state.get(name)!r}")
    if failures:
        raise RuntimeError(f"browser state failed for {expected_path}: {', '.join(failures)}")


def assert_mobile_navigation(state: dict[str, Any], path: str) -> None:
    if state.get("navDisplay") != "grid":
        raise RuntimeError(f"mobile nav is not grid on {path}: {state.get('navDisplay')!r}")
    columns = str(state.get("navColumns") or "").split()
    if len(columns) != 3:
        raise RuntimeError(f"mobile nav is not three columns on {path}: {columns!r}")


def assert_keyboard_focus(driver: WebDriver) -> None:
    driver.execute("document.activeElement && document.activeElement.blur(); document.body.focus();")
    for index in range(3):
        driver.press_tab()
        focused = driver.execute(
            """
            const e = document.activeElement;
            const s = getComputedStyle(e);
            return {
              tag: e ? e.tagName : null,
              href: e ? e.getAttribute('href') : null,
              name: e ? ((e.innerText || e.getAttribute('aria-label') || '').trim()) : '',
              outlineStyle: s.outlineStyle,
              outlineWidth: s.outlineWidth,
            };
            """
        )
        if not isinstance(focused, dict):
            raise RuntimeError(f"unexpected focus state: {focused!r}")
        width = str(focused.get("outlineWidth") or "0")
        visible = focused.get("outlineStyle") not in (None, "none") and width not in ("0px", "0")
        if focused.get("tag") != "A" or not focused.get("name") or not visible:
            raise RuntimeError(f"keyboard focus step {index + 1} is not visibly focused: {focused!r}")


def smoke(root: Path) -> None:
    chrome = chrome_binary()
    chromedriver = executable("chromedriver", "CHROMEWEBDRIVER")
    print("Chrome:", run_version(chrome, "--version"))
    print("ChromeDriver:", run_version(chromedriver, "--version"))

    with tempfile.TemporaryDirectory(prefix="hermes-tech-browser-") as temporary:
        work = Path(temporary)
        public = work / "public"
        cache = work / "cache"
        public.mkdir()
        cache.mkdir()
        build_site(root, public, cache)
        validate_internal_links(public)

        RecordingHandler.request_log = []
        handler = lambda *args, **kwargs: RecordingHandler(*args, directory=str(public), **kwargs)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"

        port = 9515
        driver_process = subprocess.Popen(
            [chromedriver, f"--port={port}", "--allowed-ips=127.0.0.1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        driver = WebDriver(f"http://127.0.0.1:{port}")
        try:
            wait_for_webdriver(driver.endpoint)
            driver.create_session(chrome)
            article = first_article_path(public)
            paths = (
                "/",
                "/digest/",
                "/ai/",
                "/agents/",
                "/digest/page/2/",
                article,
                "/how-hermes-works/",
                "/impressum/",
            )

            for width, height, mobile in ((1440, 1000, False), (375, 812, True)):
                driver.set_viewport(width, height)
                for path in paths:
                    RecordingHandler.request_log.clear()
                    driver.navigate(origin + path)
                    state = page_state(driver)
                    assert_page_state(state, path)
                    if mobile:
                        assert_mobile_navigation(state, path)
                    bad_requests = [entry for entry in RecordingHandler.request_log if entry[1] >= 400]
                    if bad_requests:
                        raise RuntimeError(f"browser request failures on {path}: {bad_requests!r}")
                    severe = [entry for entry in driver.browser_logs() if entry.get("level") == "SEVERE"]
                    if severe:
                        raise RuntimeError(f"browser console errors on {path}: {severe!r}")

            driver.set_viewport(1440, 1000)
            driver.navigate(origin + "/")
            assert_keyboard_focus(driver)
        finally:
            driver.close()
            driver_process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                driver_process.wait(timeout=5)
            if driver_process.poll() is None:
                driver_process.kill()
                driver_process.wait(timeout=5)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

    print("Hermes Tech browser smoke PASS")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Hermes Tech real-browser static regression checks.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        smoke(args.root.resolve())
    except (OSError, RuntimeError, subprocess.CalledProcessError, WebDriverError) as exc:
        print(f"KĻŪDA: browser smoke: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
