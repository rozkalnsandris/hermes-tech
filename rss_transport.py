#!/usr/bin/env python3
"""Bounded HTTPS transport for untrusted third-party RSS feeds."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

USER_AGENT = "HermesTech/1.0 (+https://tech.rozkalns.net)"
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 20
CHUNK_BYTES = 64 * 1024
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class FeedTransportError(RuntimeError):
    """Raised when a feed violates the collector's network boundary."""


@dataclass(frozen=True)
class FetchedFeed:
    body: bytes
    final_url: str
    status_code: int
    content_type: str
    redirects: int


def _target_parts(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise FeedTransportError("feed URL must use HTTPS")
    if not parsed.hostname:
        raise FeedTransportError("feed URL has no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise FeedTransportError("feed URL must not contain credentials")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise FeedTransportError(f"invalid feed URL port: {exc}") from exc
    if port != 443:
        raise FeedTransportError("feed URL must use HTTPS port 443")
    return parsed.hostname, port


def _resolve_global_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise FeedTransportError(f"DNS resolution failed for {hostname}: {exc}") from exc

    addresses: set[str] = set()
    for answer in answers:
        sockaddr = answer[4]
        if not sockaddr:
            continue
        addresses.add(str(sockaddr[0]).split("%", 1)[0])
    if not addresses:
        raise FeedTransportError(f"DNS returned no addresses for {hostname}")

    blocked: list[str] = []
    for raw in sorted(addresses):
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise FeedTransportError(f"DNS returned invalid address for {hostname}: {raw}") from exc
        if not address.is_global:
            blocked.append(raw)
    if blocked:
        raise FeedTransportError(
            f"feed hostname {hostname} resolved to non-public address(es): "
            + ", ".join(blocked)
        )
    return tuple(sorted(addresses))


def validate_public_https_target(url: str) -> tuple[str, tuple[str, ...]]:
    """Validate scheme/credentials/port and require only globally routed DNS answers."""
    hostname, port = _target_parts(url)
    return hostname, _resolve_global_addresses(hostname, port)


def _content_type(response: requests.Response) -> str:
    return (response.headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()


def _read_bounded(response: requests.Response, max_bytes: int) -> bytes:
    length = response.headers.get("Content-Length")
    if length:
        try:
            declared = int(length)
        except ValueError:
            declared = -1
        if declared > max_bytes:
            raise FeedTransportError(
                f"feed response exceeds byte limit: Content-Length={declared}, limit={max_bytes}"
            )

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise FeedTransportError(
                f"feed response exceeds byte limit while streaming: {total}>{max_bytes}"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_feed(
    url: str,
    *,
    session: requests.Session | None = None,
    max_bytes: int = MAX_FEED_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> FetchedFeed:
    """Fetch one RSS/Atom document through a fail-closed public HTTPS boundary."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")

    own_session = session is None
    client = requests.Session() if session is None else session
    if own_session:
        # Do not let ambient proxy variables silently change the collector's network boundary.
        client.trust_env = False
    client.headers.setdefault("User-Agent", USER_AGENT)
    client.headers.setdefault(
        "Accept",
        "application/rss+xml, application/atom+xml, application/xml, text/xml, text/plain;q=0.8, */*;q=0.2",
    )

    current = url
    redirects = 0
    try:
        while True:
            validate_public_https_target(current)
            try:
                response = client.get(
                    current,
                    stream=True,
                    allow_redirects=False,
                    timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                )
            except requests.RequestException as exc:
                raise FeedTransportError(f"feed request failed: {exc}") from exc

            with response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise FeedTransportError(
                            f"HTTP {response.status_code} redirect has no Location header"
                        )
                    if redirects >= max_redirects:
                        raise FeedTransportError(
                            f"feed exceeded redirect limit ({max_redirects})"
                        )
                    current = urljoin(current, location)
                    redirects += 1
                    continue

                if response.status_code != 200:
                    raise FeedTransportError(
                        f"feed returned HTTP {response.status_code}"
                    )

                body = _read_bounded(response, max_bytes)
                if not body:
                    raise FeedTransportError("feed returned an empty response body")
                return FetchedFeed(
                    body=body,
                    final_url=current,
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    redirects=redirects,
                )
    finally:
        if own_session:
            client.close()
