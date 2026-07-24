from __future__ import annotations

import subprocess

import pytest

from tracefetch.adapters.search import (
    ExaSearchProvider,
    GitHubSearchProvider,
    _parse_exa_output,
)
from tracefetch.contracts import SearchCandidate
from tracefetch.errors import FetchFailedError, InvalidInputError
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
