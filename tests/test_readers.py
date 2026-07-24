from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tracefetch.adapters.readers import DirectReader
from tracefetch.config import Policy
from tracefetch.errors import FetchFailedError, PolicyBlockedError
from tracefetch.robots import RobotsPolicy


class FixtureHandler(BaseHTTPRequestHandler):
    robots_status = 200

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/robots.txt":
            self.send_response(self.robots_status)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            if self.robots_status == 200:
                self.wfile.write(b"User-agent: TraceFetch\nDisallow: /blocked\n")
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", "/loop")
            self.end_headers()
            return
        if self.path == "/large":
            body = b"x" * 2048
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/failure":
            self.send_response(503)
            self.end_headers()
            return
        body = b"<main><h1>Local fixture</h1><p>reader result</p></main>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("ETag", '"fixture"')
        self.send_header("Set-Cookie", "secret=not-recorded")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def local_server() -> Iterator[str]:
    FixtureHandler.robots_status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def policy(**updates: object) -> Policy:
    return Policy(
        deny_private_networks=False,
        timeout_seconds=2,
        min_normalized_chars=1,
        **updates,
    )


def test_direct_reader_follows_manual_redirect_and_filters_headers() -> None:
    with local_server() as base:
        result = DirectReader().fetch(f"{base}/redirect", policy())

    assert result.final_url.endswith("/final")
    assert result.status_code == 200
    assert result.robots_status == "allowed"
    assert result.metadata["response_headers"]["etag"] == '"fixture"'
    assert "set-cookie" not in result.metadata["response_headers"]


def test_direct_reader_honors_robots_disallow() -> None:
    with local_server() as base, pytest.raises(PolicyBlockedError, match="disallows"):
        DirectReader().fetch(f"{base}/blocked", policy())


def test_direct_reader_enforces_streaming_byte_limit() -> None:
    with local_server() as base, pytest.raises(FetchFailedError, match="max_bytes") as error:
        DirectReader().fetch(f"{base}/large", policy(max_bytes=1024))
    assert error.value.retryable is False


def test_direct_reader_classifies_server_failure_as_retryable() -> None:
    with local_server() as base, pytest.raises(FetchFailedError) as error:
        DirectReader().fetch(f"{base}/failure", policy())
    assert error.value.retryable is True
    assert error.value.details["http_status"] == 503


def test_redirect_limit_is_enforced() -> None:
    with local_server() as base, pytest.raises(FetchFailedError, match="redirect limit"):
        DirectReader().fetch(f"{base}/loop", policy(max_redirects=0))


def test_robots_server_failure_fails_closed_by_default() -> None:
    with local_server() as base:
        FixtureHandler.robots_status = 500
        decision = RobotsPolicy().evaluate(f"{base}/page", policy())
    assert decision.status == "unavailable"
    assert decision.allowed is False


def test_robots_server_failure_can_be_explicitly_fail_open() -> None:
    with local_server() as base:
        FixtureHandler.robots_status = 500
        decision = RobotsPolicy().evaluate(f"{base}/page", policy(robots_fail_closed=False))
    assert decision.allowed is True


def test_robots_can_be_explicitly_disabled_without_network() -> None:
    decision = RobotsPolicy().evaluate("https://example.invalid/page", Policy(obey_robots=False))
    assert decision.status == "unchecked"
    assert decision.allowed is True
