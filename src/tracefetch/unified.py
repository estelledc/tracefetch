from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from tracefetch.contracts import (
    CommandProviderSpec,
    ProviderManifest,
    SearchResultAttempt,
    SearchResultCandidate,
    SearchResultsEnvelope,
)
from tracefetch.errors import (
    PolicyBlockedError,
    SearchFailedError,
    TraceFetchError,
)
from tracefetch.providers import provider_availability, run_command_provider
from tracefetch.search import search_sources
from tracefetch.workspace import search_workspace

OFFICIAL_DOMAINS = {
    "developer.apple.com",
    "developer.mozilla.org",
    "docs.anthropic.com",
    "docs.github.com",
    "docs.python.org",
    "modelcontextprotocol.io",
    "swift.org",
    "typescriptlang.org",
}


def search_everywhere(
    query: str,
    *,
    scopes: list[str] | None,
    root: Path,
    public_provider: str,
    limit: int,
    provider_manifest: ProviderManifest | None = None,
    provider_base_dir: Path | None = None,
    allow_sensitive: bool = False,
    include_globs: list[str] | None = None,
) -> SearchResultsEnvelope:
    normalized_query = " ".join(query.split())
    if not normalized_query:
        from tracefetch.errors import InvalidInputError

        raise InvalidInputError("search query must not be empty")
    manifest = provider_manifest or ProviderManifest()
    base_dir = (provider_base_dir or Path.cwd()).resolve()
    expanded = _expand_scopes(scopes)
    specs_by_scope = _provider_specs_by_scope(manifest)
    known_scopes = {"local", "public", *specs_by_scope}
    unknown = [scope for scope in expanded if scope not in known_scopes]
    if unknown:
        from tracefetch.errors import AdapterUnavailableError

        raise AdapterUnavailableError(f"unknown search scope: {unknown[0]}")

    selected_specs = [spec for scope in expanded for spec in specs_by_scope.get(scope, [])]
    sensitive = [
        spec for spec in selected_specs if spec.sensitivity in {"account-visible", "internal"}
    ]
    if sensitive and not allow_sensitive:
        raise PolicyBlockedError(
            "account-visible and internal providers require --allow-sensitive",
            details={"providers": [spec.name for spec in sensitive]},
        )
    isolated = [spec for spec in selected_specs if spec.isolated]
    if isolated and (
        len(expanded) != 1
        or len(isolated) != len(selected_specs)
        or any(spec.scope != expanded[0] for spec in isolated)
    ):
        raise PolicyBlockedError(
            "isolated providers cannot be combined with another search scope",
            details={"providers": [spec.name for spec in isolated]},
        )

    groups: list[list[SearchResultCandidate]] = []
    attempts: list[SearchResultAttempt] = []
    warnings: list[str] = []
    for scope in expanded:
        if scope == "local":
            candidates, attempt, local_warnings = search_workspace(
                root,
                normalized_query,
                limit=limit,
                include_globs=include_globs,
            )
            groups.append(candidates)
            attempts.append(attempt)
            warnings.extend(local_warnings)
            continue
        if scope == "public":
            candidates, public_attempts, public_warnings = _search_public(
                normalized_query,
                provider=public_provider,
                limit=limit,
            )
            groups.append(candidates)
            attempts.extend(public_attempts)
            warnings.extend(public_warnings)
            continue
        for spec in specs_by_scope[scope]:
            candidates, attempt, provider_warnings = _search_command_provider(
                spec,
                query=normalized_query,
                limit=limit,
                base_dir=base_dir,
            )
            groups.append(candidates)
            attempts.append(attempt)
            warnings.extend(provider_warnings)

    candidates = _round_robin(groups, limit)
    if not candidates and attempts and all(item.status != "success" for item in attempts):
        raise SearchFailedError(
            "every selected search provider failed or was unavailable",
            details={
                "attempts": [
                    {
                        "provider": item.provider,
                        "scope": item.scope,
                        "status": item.status,
                        "code": item.code,
                    }
                    for item in attempts
                ]
            },
        )
    sensitivity = _result_sensitivity(selected_specs)
    warnings.append(
        "search results are discovery candidates; fetch and verify public sources "
        "before citing content"
    )
    return SearchResultsEnvelope(
        query=normalized_query,
        scopes=expanded,
        sensitivity=sensitivity,
        candidates=candidates,
        attempts=attempts,
        warnings=list(dict.fromkeys(warnings)),
    )


def _expand_scopes(raw_scopes: list[str] | None) -> list[str]:
    requested = raw_scopes or ["local"]
    if "auto" in requested and len(requested) > 1:
        from tracefetch.errors import InvalidInputError

        raise InvalidInputError("auto cannot be combined with another scope")
    expanded: list[str] = []
    for scope in requested:
        additions = ["local", "public"] if scope == "auto" else [scope]
        for item in additions:
            if item not in expanded:
                expanded.append(item)
    return expanded


def _provider_specs_by_scope(
    manifest: ProviderManifest,
) -> dict[str, list[CommandProviderSpec]]:
    grouped: dict[str, list[CommandProviderSpec]] = {}
    for spec in manifest.providers:
        grouped.setdefault(spec.scope, []).append(spec)
    return grouped


def _search_public(
    query: str,
    *,
    provider: str,
    limit: int,
) -> tuple[list[SearchResultCandidate], list[SearchResultAttempt], list[str]]:
    envelope = search_sources(
        query,
        provider=provider,
        limit=limit,
        fail_if_all=False,
    )
    candidates = [
        SearchResultCandidate(
            rank=index,
            title=item.title,
            locator=item.url,
            snippet=item.snippet,
            provider=item.provider,
            scope="public",
            source_class=_public_source_class(item.url),
            evidence_state="candidate-only",
            sensitivity="public",
            published_at=item.published_at,
            metadata={**item.metadata, "provider_rank": item.rank},
        )
        for index, item in enumerate(envelope.candidates, start=1)
    ]
    attempts = [
        SearchResultAttempt(
            provider=item.provider,
            scope="public",
            status=item.status,
            candidate_count=item.candidate_count,
            backend=item.provider,
            code=item.code,
            message=item.message,
        )
        for item in envelope.attempts
    ]
    warnings = []
    if any(item.status == "failed" for item in attempts) and candidates:
        warnings.append("public search returned partial results after a provider failure")
    return candidates, attempts, warnings


def _search_command_provider(
    spec: CommandProviderSpec,
    *,
    query: str,
    limit: int,
    base_dir: Path,
) -> tuple[list[SearchResultCandidate], SearchResultAttempt, list[str]]:
    available, backend = provider_availability(spec, base_dir=base_dir)
    if not available:
        return (
            [],
            SearchResultAttempt(
                provider=spec.name,
                scope=spec.scope,
                status="skipped",
                backend=backend,
                code="adapter_unavailable",
                message="provider executable is unavailable",
            ),
            [],
        )
    try:
        response = run_command_provider(
            spec,
            query=query,
            limit=limit,
            base_dir=base_dir,
        )
    except TraceFetchError as exc:
        return (
            [],
            SearchResultAttempt(
                provider=spec.name,
                scope=spec.scope,
                status="failed",
                backend=backend,
                code=exc.code,
                message=exc.message,
            ),
            [],
        )
    evidence_state: Literal["candidate-only", "candidate-only-internal"] = (
        "candidate-only-internal" if spec.sensitivity == "internal" else "candidate-only"
    )
    candidates = [
        SearchResultCandidate(
            rank=index,
            title=item.title,
            locator=item.locator,
            snippet=item.snippet,
            provider=spec.name,
            scope=spec.scope,
            source_class=spec.source_class,
            evidence_state=evidence_state,
            sensitivity=spec.sensitivity,
            published_at=item.published_at,
            metadata={**item.metadata, "provider_rank": index},
        )
        for index, item in enumerate(response.candidates, start=1)
    ]
    return (
        candidates,
        SearchResultAttempt(
            provider=spec.name,
            scope=spec.scope,
            status="success",
            candidate_count=len(candidates),
            backend=backend,
        ),
        response.warnings,
    )


def _result_sensitivity(
    specs: list[CommandProviderSpec],
) -> Literal["public-or-project", "account-visible", "internal"]:
    if any(spec.sensitivity == "internal" for spec in specs):
        return "internal"
    if any(spec.sensitivity == "account-visible" for spec in specs):
        return "account-visible"
    return "public-or-project"


def _round_robin(
    groups: list[list[SearchResultCandidate]],
    limit: int,
) -> list[SearchResultCandidate]:
    collected: list[SearchResultCandidate] = []
    seen: set[str] = set()
    index = 0
    while len(collected) < limit and any(index < len(group) for group in groups):
        for group in groups:
            if index >= len(group):
                continue
            candidate = group[index]
            canonical = candidate.locator.rstrip("/")
            if canonical in seen:
                continue
            seen.add(canonical)
            candidate.rank = len(collected) + 1
            collected.append(candidate)
            if len(collected) >= limit:
                break
        index += 1
    return collected


def _public_source_class(locator: str) -> str:
    host = (urlparse(locator).hostname or "").casefold()
    if any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_DOMAINS):
        return "official-candidate"
    if host == "github.com" or host.endswith(".github.com"):
        return "open-source-candidate"
    return "web-candidate"
