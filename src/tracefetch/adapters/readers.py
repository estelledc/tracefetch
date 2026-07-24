from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from tracefetch.adapters.base import ReaderResult
from tracefetch.config import Policy
from tracefetch.errors import AdapterUnavailableError, FetchFailedError, PolicyBlockedError
from tracefetch.robots import RobotsPolicy
from tracefetch.security import validate_public_url


def _now() -> datetime:
    return datetime.now(UTC)


class DirectReader:
    name = "direct"
    remote = False
    authenticated = False

    def __init__(self, robots: RobotsPolicy | None = None) -> None:
        self.robots = robots or RobotsPolicy()

    def available(self) -> tuple[bool, str]:
        return True, "built-in HTTP reader"

    def fetch(self, url: str, policy: Policy) -> ReaderResult:
        requested = validate_public_url(url, policy)
        current = requested
        decision = self.robots.evaluate(current, policy)
        if not decision.allowed:
            raise PolicyBlockedError(decision.message, details={"robots_status": decision.status})
        headers = {
            "User-Agent": policy.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/json,text/plain,"
                "application/pdf,*/*;q=0.1"
            ),
        }
        with httpx.Client(
            timeout=policy.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for _ in range(policy.max_redirects + 1):
                try:
                    with client.stream("GET", current, headers=headers) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise FetchFailedError("redirect response lacks Location header")
                            current = validate_public_url(urljoin(current, location), policy)
                            decision = self.robots.evaluate(current, policy)
                            if not decision.allowed:
                                raise PolicyBlockedError(
                                    decision.message,
                                    details={"robots_status": decision.status},
                                )
                            continue
                        if not response.is_success:
                            raise FetchFailedError(
                                f"HTTP status {response.status_code}",
                                retryable=response.status_code in {408, 425, 429}
                                or response.status_code >= 500,
                                details={"http_status": response.status_code},
                            )
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > policy.max_bytes:
                                raise FetchFailedError(
                                    "response exceeded max_bytes",
                                    retryable=False,
                                    details={"max_bytes": policy.max_bytes},
                                )
                            chunks.append(chunk)
                        return ReaderResult(
                            adapter=self.name,
                            requested_url=requested,
                            final_url=current,
                            status_code=response.status_code,
                            content_type=response.headers.get(
                                "content-type", "application/octet-stream"
                            ),
                            body=b"".join(chunks),
                            fetched_at=_now(),
                            robots_status=decision.status,
                            metadata={"response_headers": _safe_headers(response.headers)},
                        )
                except httpx.HTTPError as exc:
                    raise FetchFailedError(str(exc), retryable=True) from exc
        raise FetchFailedError("redirect limit exceeded", retryable=False)


class JinaReader:
    name = "jina"
    remote = True
    authenticated = False

    def __init__(self, robots: RobotsPolicy | None = None) -> None:
        self.robots = robots or RobotsPolicy()

    def available(self) -> tuple[bool, str]:
        return True, "public Jina Reader service"

    def fetch(self, url: str, policy: Policy) -> ReaderResult:
        if not policy.allow_remote_adapters:
            raise PolicyBlockedError("remote adapters are disabled by policy")
        target = validate_public_url(url, policy)
        decision = self.robots.evaluate(target, policy)
        if not decision.allowed:
            raise PolicyBlockedError(decision.message, details={"robots_status": decision.status})
        reader_url = f"https://r.jina.ai/{target}"
        headers = {"User-Agent": policy.user_agent, "Accept": "text/markdown,text/plain"}
        try:
            with httpx.Client(
                timeout=policy.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.get(reader_url, headers=headers)
        except httpx.HTTPError as exc:
            raise FetchFailedError(str(exc), retryable=True) from exc
        if not response.is_success:
            raise FetchFailedError(
                f"Jina Reader status {response.status_code}",
                retryable=response.status_code in {408, 425, 429} or response.status_code >= 500,
                details={"http_status": response.status_code},
            )
        if len(response.content) > policy.max_bytes:
            raise FetchFailedError("Jina response exceeded max_bytes", retryable=False)
        markdown = response.text
        return ReaderResult(
            adapter=self.name,
            requested_url=target,
            final_url=target,
            status_code=response.status_code,
            content_type="text/markdown; charset=utf-8",
            body=markdown.encode("utf-8"),
            fetched_at=_now(),
            robots_status=decision.status,
            provided_markdown=markdown,
            metadata={"remote_service": "r.jina.ai", "raw_origin_preserved": False},
        )


class FirecrawlReader:
    name = "firecrawl"
    remote = True
    authenticated = True

    def __init__(self, robots: RobotsPolicy | None = None) -> None:
        self.robots = robots or RobotsPolicy()

    def available(self) -> tuple[bool, str]:
        present = bool(os.environ.get("FIRECRAWL_API_KEY"))
        return present, "FIRECRAWL_API_KEY configured" if present else "FIRECRAWL_API_KEY missing"

    def fetch(self, url: str, policy: Policy) -> ReaderResult:
        if not policy.allow_remote_adapters:
            raise PolicyBlockedError("remote adapters are disabled by policy")
        if not policy.allow_authenticated_adapters:
            raise PolicyBlockedError("authenticated adapters are disabled by policy")
        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            raise AdapterUnavailableError("FIRECRAWL_API_KEY is not configured")
        target = validate_public_url(url, policy)
        decision = self.robots.evaluate(target, policy)
        if not decision.allowed:
            raise PolicyBlockedError(decision.message, details={"robots_status": decision.status})
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"url": target, "formats": ["markdown", "rawHtml"]}
        try:
            with httpx.Client(
                timeout=policy.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    "https://api.firecrawl.dev/v2/scrape", headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise FetchFailedError(str(exc), retryable=True) from exc
        if not response.is_success:
            raise FetchFailedError(
                f"Firecrawl status {response.status_code}",
                retryable=response.status_code in {408, 425, 429} or response.status_code >= 500,
                details={"http_status": response.status_code},
            )
        try:
            decoded: dict[str, Any] = response.json()
            data = decoded.get("data", decoded)
            markdown = str(data.get("markdown") or "")
            raw_html = str(data.get("rawHtml") or data.get("html") or "")
            metadata = data.get("metadata") or {}
        except (ValueError, AttributeError) as exc:
            raise FetchFailedError("invalid Firecrawl JSON response", retryable=False) from exc
        if not isinstance(metadata, dict):
            raise FetchFailedError("invalid Firecrawl metadata", retryable=False)
        if not markdown:
            raise FetchFailedError("Firecrawl response has no markdown", retryable=False)
        body = raw_html.encode("utf-8") if raw_html else markdown.encode("utf-8")
        if len(body) > policy.max_bytes:
            raise FetchFailedError("Firecrawl raw output exceeded max_bytes", retryable=False)
        final_url = validate_public_url(
            str(metadata.get("sourceURL") or metadata.get("url") or target), policy
        )
        try:
            status_code = int(metadata.get("statusCode") or 200)
        except (TypeError, ValueError) as exc:
            raise FetchFailedError("invalid Firecrawl status code", retryable=False) from exc
        return ReaderResult(
            adapter=self.name,
            requested_url=target,
            final_url=final_url,
            status_code=status_code,
            content_type="text/html; charset=utf-8" if raw_html else "text/markdown; charset=utf-8",
            body=body,
            fetched_at=_now(),
            robots_status=decision.status,
            provided_markdown=markdown,
            metadata={
                "remote_service": "firecrawl",
                "raw_origin_preserved": False,
                "remote_extracted_html": bool(raw_html),
            },
        )


def _safe_headers(headers: httpx.Headers) -> dict[str, str]:
    allow = {"content-type", "content-length", "etag", "last-modified", "cache-control"}
    return {key.lower(): value for key, value in headers.items() if key.lower() in allow}
