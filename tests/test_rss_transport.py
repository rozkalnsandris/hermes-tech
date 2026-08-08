from __future__ import annotations

from pathlib import Path
import socket
import unittest
from unittest import mock

import rss_transport


ROOT = Path(__file__).resolve().parents[1]


def dns_answer(address: str, port: int = 443):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


class FakeResponse:
    def __init__(self, status_code: int, *, headers=None, chunks=None) -> None:
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._chunks = list(chunks or [])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield from self._chunks


class FakeSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class RssTransportTests(unittest.TestCase):
    def test_active_feed_configuration_is_https_only(self) -> None:
        urls = []
        for raw in (ROOT / "feeds.txt").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) >= 2:
                urls.append(parts[1])
        self.assertTrue(urls)
        self.assertTrue(all(url.startswith("https://") for url in urls), urls)

    def test_target_rejects_http_credentials_and_nonstandard_port(self) -> None:
        for url in (
            "http://feeds.example/rss.xml",
            "https://user:password@feeds.example/rss.xml",
            "https://feeds.example:8443/rss.xml",
        ):
            with self.subTest(url=url):
                with self.assertRaises(rss_transport.FeedTransportError):
                    rss_transport._target_parts(url)

    def test_target_rejects_non_global_dns_answers(self) -> None:
        blocked = (
            "127.0.0.1",
            "10.0.0.7",
            "169.254.1.2",
            "100.64.0.1",
            "::1",
            "fe80::1",
        )
        for address in blocked:
            with self.subTest(address=address):
                with mock.patch(
                    "rss_transport.socket.getaddrinfo",
                    return_value=dns_answer(address),
                ):
                    with self.assertRaises(rss_transport.FeedTransportError):
                        rss_transport.validate_public_https_target(
                            "https://feeds.example/rss.xml"
                        )

    def test_redirect_target_is_revalidated_before_second_request(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    302,
                    headers={"Location": "https://internal.example/feed.xml"},
                )
            ]
        )

        def resolve(hostname, port, **kwargs):
            del kwargs
            if hostname == "feeds.example":
                return dns_answer("203.0.113.7", port)
            if hostname == "internal.example":
                return dns_answer("127.0.0.1", port)
            raise AssertionError(hostname)

        # 203.0.113.0/24 is documentation space and not global according to ipaddress,
        # so use a known globally routed test value for the initial mocked answer.
        def resolve_global_then_private(hostname, port, **kwargs):
            del kwargs
            if hostname == "feeds.example":
                return dns_answer("8.8.8.8", port)
            if hostname == "internal.example":
                return dns_answer("127.0.0.1", port)
            raise AssertionError(hostname)

        with mock.patch(
            "rss_transport.socket.getaddrinfo",
            side_effect=resolve_global_then_private,
        ):
            with self.assertRaises(rss_transport.FeedTransportError):
                rss_transport.fetch_feed(
                    "https://feeds.example/rss.xml",
                    session=session,
                )
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_declared_content_length_over_limit_is_rejected(self) -> None:
        session = FakeSession(
            [FakeResponse(200, headers={"Content-Length": "101"}, chunks=[b"x"])]
        )
        with mock.patch(
            "rss_transport.socket.getaddrinfo",
            return_value=dns_answer("8.8.8.8"),
        ):
            with self.assertRaises(rss_transport.FeedTransportError):
                rss_transport.fetch_feed(
                    "https://feeds.example/rss.xml",
                    session=session,
                    max_bytes=100,
                )

    def test_streaming_body_over_limit_is_rejected(self) -> None:
        session = FakeSession([FakeResponse(200, chunks=[b"a" * 60, b"b" * 41])])
        with mock.patch(
            "rss_transport.socket.getaddrinfo",
            return_value=dns_answer("8.8.8.8"),
        ):
            with self.assertRaises(rss_transport.FeedTransportError):
                rss_transport.fetch_feed(
                    "https://feeds.example/rss.xml",
                    session=session,
                    max_bytes=100,
                )

    def test_success_returns_bytes_and_transport_metadata(self) -> None:
        body = b"<rss><channel><title>Hermes test</title></channel></rss>"
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    headers={
                        "Content-Type": "application/rss+xml; charset=utf-8",
                        "Content-Length": str(len(body)),
                    },
                    chunks=[body[:20], body[20:]],
                )
            ]
        )
        with mock.patch(
            "rss_transport.socket.getaddrinfo",
            return_value=dns_answer("8.8.8.8"),
        ):
            fetched = rss_transport.fetch_feed(
                "https://feeds.example/rss.xml",
                session=session,
            )
        self.assertEqual(fetched.body, body)
        self.assertEqual(fetched.final_url, "https://feeds.example/rss.xml")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.content_type, "application/rss+xml")
        self.assertEqual(fetched.redirects, 0)
        self.assertEqual(session.calls[0][1]["timeout"], (5, 20))
        self.assertTrue(session.calls[0][1]["stream"])
        self.assertFalse(session.calls[0][1]["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
