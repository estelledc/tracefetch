from __future__ import annotations

import json
import subprocess

import pytest

from tracefetch.adapters.search import (
    ExaSearchProvider,
    GitHubSearchProvider,
    _compact_exa_snippet,
    _compact_provider_error,
    _github_query_variants,
    _parse_exa_output,
)
from tracefetch.contracts import SearchCandidate
from tracefetch.errors import FetchFailedError, InvalidInputError, RateLimitedError
from tracefetch.search import search_sources


def candidate(provider: str, index: int) -> SearchCandidate:
    return SearchCandidate(
        rank=index,
        title=f"{provider}-{index}",
        url=f"https://{provider}.example/{index}",
        provider=provider,
    )


def test_parse_exa_text_contract() -> None:
    raw = """Title: First source
URL: https://example.com/first
Published: 2026-01-02
Highlights: Evidence-ready text.
---
Title: Second source
URL: https://example.com/second
Published: N/A
Highlights: Another result.
"""
    results = _parse_exa_output(raw, 5)
    assert [item.title for item in results] == ["First source", "Second source"]
    assert results[0].published_at == "2026-01-02"
    assert results[1].published_at is None


def test_all_provider_results_are_round_robin_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ExaSearchProvider, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(GitHubSearchProvider, "available", lambda self: (True, "ok"))

    def exa_search(self: ExaSearchProvider, query: str, limit: int) -> list[SearchCandidate]:
        calls.append("exa")
        return [candidate("exa", 1), candidate("exa", 2)]

    def github_search(self: GitHubSearchProvider, query: str, limit: int) -> list[SearchCandidate]:
        calls.append("github")
        return [candidate("github", 1), candidate("github", 2)]

    monkeypatch.setattr(ExaSearchProvider, "search", exa_search)
    monkeypatch.setattr(GitHubSearchProvider, "search", github_search)

    envelope = search_sources("provenance", provider="all", limit=4)

    assert calls == ["exa", "github"]
    assert [item.provider for item in envelope.candidates] == [
        "exa",
        "github",
        "exa",
        "github",
    ]
    assert [item.rank for item in envelope.candidates] == [1, 2, 3, 4]
    assert [item.candidate_count for item in envelope.attempts] == [2, 2]


def test_auto_falls_back_after_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ExaSearchProvider, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(GitHubSearchProvider, "available", lambda self: (True, "ok"))
    monkeypatch.setattr(
        ExaSearchProvider,
        "search",
        lambda self, query, limit: (_ for _ in ()).throw(FetchFailedError("down")),
    )
    monkeypatch.setattr(
        GitHubSearchProvider,
        "search",
        lambda self, query, limit: [candidate("github", 1)],
    )

    envelope = search_sources("provenance", provider="auto", limit=3)

    assert [attempt.status for attempt in envelope.attempts] == ["failed", "success"]
    assert [attempt.candidate_count for attempt in envelope.attempts] == [0, 1]
    assert envelope.candidates[0].provider == "github"


def test_blank_query_is_rejected() -> None:
    with pytest.raises(InvalidInputError):
        search_sources("   ", provider="auto", limit=3)


def test_subprocess_timeout_becomes_stable_adapter_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracefetch.adapters.search.shutil.which", lambda command: "/bin/tool")

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("tool", 60)

    monkeypatch.setattr("tracefetch.adapters.search.subprocess.run", timeout)
    with pytest.raises(FetchFailedError, match="execution failed"):
        ExaSearchProvider().search("query", 1)


def test_exa_rate_limit_is_classified_without_forwarding_tool_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracefetch.adapters.search.shutil.which", lambda command: "/bin/tool")
    raw_error = (
        "[mcporter] HTTP 429: You've hit Exa's free MCP rate limit. "
        "Use exaApiKey=secret-value\n"
        "StreamableHTTPError: internal transport detail\n"
        "    at send (/private/tool.js:123:4)\n"
    ) * 20
    monkeypatch.setattr(
        "tracefetch.adapters.search.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr=raw_error
        ),
    )

    with pytest.raises(RateLimitedError) as caught:
        ExaSearchProvider().search("query", 1)

    assert caught.value.code == "rate_limited"
    assert caught.value.message == "upstream search rate limit reached (HTTP 429)"
    assert "secret-value" not in caught.value.message
    assert "tool.js" not in caught.value.message


def test_generic_provider_error_is_redacted_and_bounded() -> None:
    raw = "Authorization: Bearer private-token\n" + ("transport detail " * 80)

    message, rate_limited = _compact_provider_error(raw, fallback="provider failed")

    assert rate_limited is False
    assert "private-token" not in message
    assert "[redacted]" in message
    assert len(message) <= 320
    assert message.endswith("[truncated]")


def test_github_relaxes_an_empty_natural_language_query_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracefetch.adapters.search.shutil.which", lambda command: "/bin/gh")
    calls: list[list[str]] = []
    responses: list[object] = [
        [],
        [
            {
                "fullName": "swiftlang/swift",
                "description": "The Swift Programming Language",
                "stargazersCount": 70000,
                "url": "https://github.com/swiftlang/swift",
                "updatedAt": "2026-07-24T00:00:00Z",
                "license": {"key": "apache-2.0"},
            }
        ],
    ]

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(responses.pop(0)))

    monkeypatch.setattr("tracefetch.adapters.search.subprocess.run", run)

    candidates = GitHubSearchProvider().search(
        "Swift concurrency MainActor Sendable actor isolation", 5
    )

    assert [call[3] for call in calls] == [
        "Swift concurrency MainActor Sendable actor isolation",
        "Swift concurrency",
    ]
    assert "--sort" not in calls[0]
    assert calls[1][-2:] == ["--sort", "stars"]
    assert candidates[0].metadata["query_variant"] == "Swift concurrency"
    assert candidates[0].metadata["query_relaxed"] is True


def test_github_keeps_a_nonempty_exact_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tracefetch.adapters.search.shutil.which", lambda command: "/bin/gh")
    calls: list[list[str]] = []
    payload = [
        {
            "fullName": "swiftlang/swift-evolution",
            "description": "Evolution of the Swift language",
            "stargazersCount": 16000,
            "url": "https://github.com/swiftlang/swift-evolution",
            "updatedAt": "2026-07-24T00:00:00Z",
            "license": {"key": "apache-2.0"},
        }
    ]

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))

    monkeypatch.setattr("tracefetch.adapters.search.subprocess.run", run)

    candidates = GitHubSearchProvider().search("Swift concurrency", 5)

    assert len(calls) == 1
    assert calls[0][3] == "Swift concurrency"
    assert candidates[0].metadata["query_variant"] == "Swift concurrency"
    assert candidates[0].metadata["query_relaxed"] is False


def test_github_query_relaxation_removes_leading_stopwords() -> None:
    assert _github_query_variants("how to build Swift concurrency examples") == [
        "how to build Swift concurrency examples",
        "Swift concurrency",
    ]
    assert _github_query_variants("AI browser device automation evidence protocol") == [
        "AI browser device automation evidence protocol",
        "browser automation",
    ]


def test_exa_snippet_compaction_removes_noise_and_bounds_context() -> None:
    noisy = "Heading\n...\nHeading\n\n" + ("detail " * 400)
    compact = _compact_exa_snippet(noisy)
    assert "\n...\n" not in compact
    assert compact.count("Heading") == 1
    assert len(compact) <= 1_200
    assert compact.endswith("[truncated]")
    parsed = _parse_exa_output(
        "Title: Result\nURL: https://example.com/\nHighlights: " + noisy,
        1,
    )
    assert parsed[0].metadata["snippet_truncated"] is True


def test_programmatic_search_provider_can_be_injected() -> None:
    class FixtureProvider:
        name = "fixture"

        def available(self) -> tuple[bool, str]:
            return True, "fixture"

        def search(self, query: str, limit: int) -> list[SearchCandidate]:
            return [candidate("fixture", 1)]

    envelope = search_sources(
        "query",
        provider="fixture",
        limit=1,
        adapters={"fixture": FixtureProvider()},
    )

    assert envelope.candidates[0].provider == "fixture"
