from __future__ import annotations

from collections.abc import Mapping

from tracefetch.adapters.base import SearchProvider
from tracefetch.adapters.search import ExaSearchProvider, GitHubSearchProvider
from tracefetch.contracts import SearchAttempt, SearchCandidate, SearchEnvelope
from tracefetch.errors import AdapterUnavailableError, InvalidInputError, TraceFetchError


def search_sources(
    query: str,
    *,
    provider: str,
    limit: int,
    adapters: Mapping[str, SearchProvider] | None = None,
    fail_if_all: bool = True,
) -> SearchEnvelope:
    query = query.strip()
    if not query:
        raise InvalidInputError("search query must not be empty")
    if limit < 1:
        raise InvalidInputError("search limit must be greater than zero")
    registry: dict[str, SearchProvider] = {
        "exa": ExaSearchProvider(),
        "github": GitHubSearchProvider(),
    }
    for name, adapter in (adapters or {}).items():
        if name in registry:
            raise InvalidInputError(f"search provider name is reserved: {name}")
        if adapter.name != name:
            raise InvalidInputError(f"search provider key {name!r} does not match {adapter.name!r}")
        registry[name] = adapter
    if provider not in {"auto", "all", *registry.keys()}:
        raise AdapterUnavailableError(f"unknown search provider: {provider}")
    providers = list(registry) if provider == "all" else [provider]
    if provider == "auto":
        providers = ["exa", "github"]

    attempts: list[SearchAttempt] = []
    provider_results: list[list[SearchCandidate]] = []
    last_error: TraceFetchError | None = None
    for name in providers:
        adapter = registry[name]
        available, reason = adapter.available()
        if not available:
            attempts.append(
                SearchAttempt(
                    provider=name,
                    status="skipped",
                    code="adapter_unavailable",
                    message=reason,
                )
            )
            continue
        try:
            candidates = adapter.search(query, limit)
            attempts.append(
                SearchAttempt(
                    provider=name,
                    status="success",
                    candidate_count=len(candidates),
                )
            )
            provider_results.append(candidates)
        except TraceFetchError as exc:
            last_error = exc
            attempts.append(
                SearchAttempt(
                    provider=name,
                    status="failed",
                    code=exc.code,
                    message=exc.message,
                )
            )
        if provider == "auto" and provider_results and provider_results[-1]:
            break
    collected = _round_robin(provider_results, limit)
    if (
        not collected
        and fail_if_all
        and last_error is not None
        and all(attempt.status != "success" for attempt in attempts)
    ):
        raise last_error
    return SearchEnvelope(query=query, candidates=collected, attempts=attempts)


def _round_robin(groups: list[list[SearchCandidate]], limit: int) -> list[SearchCandidate]:
    collected: list[SearchCandidate] = []
    seen: set[str] = set()
    index = 0
    while len(collected) < limit and any(index < len(group) for group in groups):
        for group in groups:
            if index >= len(group):
                continue
            candidate = group[index]
            canonical = candidate.url.rstrip("/")
            if canonical in seen:
                continue
            seen.add(canonical)
            candidate.rank = len(collected) + 1
            collected.append(candidate)
            if len(collected) >= limit:
                break
        index += 1
    return collected
