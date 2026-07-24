from __future__ import annotations

from pathlib import Path

import pytest

from tracefetch.contracts import (
    CommandProviderSpec,
    ProviderCandidate,
    ProviderManifest,
    ProviderResponse,
    SearchAttempt,
    SearchCandidate,
    SearchEnvelope,
    SearchResultAttempt,
    SearchResultCandidate,
)
from tracefetch.errors import AdapterUnavailableError, PolicyBlockedError, SearchFailedError
from tracefetch.unified import search_everywhere


def _local_result() -> tuple[list[SearchResultCandidate], SearchResultAttempt, list[str]]:
    return (
        [
            SearchResultCandidate(
                rank=1,
                title="README.md",
                locator="README.md:4",
                snippet="Unified search",
                provider="workspace",
                scope="local",
                source_class="workspace-source",
                evidence_state="local-source-match",
                sensitivity="project",
            )
        ],
        SearchResultAttempt(
            provider="workspace",
            scope="local",
            status="success",
            candidate_count=1,
            backend="fixture",
        ),
        [],
    )


def test_auto_round_robins_local_and_public_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tracefetch.unified.search_workspace", lambda *args, **kwargs: _local_result()
    )
    public = SearchEnvelope(
        query="unified search",
        candidates=[
            SearchCandidate(
                rank=1,
                title="Official docs",
                url="https://docs.python.org/3/",
                provider="exa",
            ),
            SearchCandidate(
                rank=2,
                title="Repository",
                url="https://github.com/example/project",
                provider="github",
            ),
        ],
        attempts=[
            SearchAttempt(provider="exa", status="success", candidate_count=1),
            SearchAttempt(provider="github", status="success", candidate_count=1),
        ],
    )

    def public_search(*args: object, **kwargs: object) -> SearchEnvelope:
        assert kwargs["provider"] == "all"
        assert kwargs["fail_if_all"] is False
        return public

    monkeypatch.setattr("tracefetch.unified.search_sources", public_search)

    envelope = search_everywhere(
        "unified search",
        scopes=["auto"],
        root=tmp_path,
        public_provider="all",
        limit=3,
    )

    assert envelope.scopes == ["local", "public"]
    assert [item.scope for item in envelope.candidates] == ["local", "public", "public"]
    assert envelope.candidates[1].source_class == "official-candidate"
    assert envelope.candidates[2].source_class == "open-source-candidate"
    assert [item.rank for item in envelope.candidates] == [1, 2, 3]


def test_sensitive_provider_requires_opt_in_and_internal_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    papers = CommandProviderSpec(
        name="papers",
        scope="papers",
        command=["provider"],
        sensitivity="account-visible",
        source_class="paper-candidate",
    )
    internal = CommandProviderSpec(
        name="internal",
        scope="internal",
        command=["provider"],
        sensitivity="internal",
        source_class="internal-candidate",
        isolated=True,
    )
    manifest = ProviderManifest(providers=[papers, internal])
    monkeypatch.setattr(
        "tracefetch.unified.provider_availability", lambda *args, **kwargs: (True, "fixture")
    )

    def response(spec: CommandProviderSpec, **kwargs: object) -> ProviderResponse:
        return ProviderResponse(
            provider=spec.name,
            scope=spec.scope,
            candidates=[
                ProviderCandidate(
                    title="Candidate",
                    locator=f"opaque:{spec.name}:1",
                    snippet="bounded",
                )
            ],
        )

    monkeypatch.setattr("tracefetch.unified.run_command_provider", response)

    with pytest.raises(PolicyBlockedError, match="allow-sensitive"):
        search_everywhere(
            "query",
            scopes=["papers"],
            root=tmp_path,
            public_provider="all",
            limit=2,
            provider_manifest=manifest,
        )

    papers_result = search_everywhere(
        "query",
        scopes=["papers"],
        root=tmp_path,
        public_provider="all",
        limit=2,
        provider_manifest=manifest,
        allow_sensitive=True,
    )
    assert papers_result.sensitivity == "account-visible"
    assert papers_result.candidates[0].evidence_state == "candidate-only"

    with pytest.raises(PolicyBlockedError, match="cannot be combined"):
        search_everywhere(
            "query",
            scopes=["local", "internal"],
            root=tmp_path,
            public_provider="all",
            limit=2,
            provider_manifest=manifest,
            allow_sensitive=True,
        )

    internal_result = search_everywhere(
        "query",
        scopes=["internal"],
        root=tmp_path,
        public_provider="all",
        limit=2,
        provider_manifest=manifest,
        allow_sensitive=True,
    )
    assert internal_result.sensitivity == "internal"
    assert internal_result.candidates[0].evidence_state == "candidate-only-internal"

    mixed_manifest = ProviderManifest(
        providers=[
            internal,
            papers.model_copy(update={"name": "public-peer", "scope": "internal"}),
        ]
    )
    with pytest.raises(PolicyBlockedError, match="cannot be combined"):
        search_everywhere(
            "query",
            scopes=["internal"],
            root=tmp_path,
            public_provider="all",
            limit=2,
            provider_manifest=mixed_manifest,
            allow_sensitive=True,
        )


def test_unknown_or_unavailable_scope_fails_with_stable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AdapterUnavailableError, match="unknown search scope"):
        search_everywhere(
            "query",
            scopes=["missing"],
            root=tmp_path,
            public_provider="all",
            limit=2,
        )

    manifest = ProviderManifest(
        providers=[
            CommandProviderSpec(
                name="papers",
                scope="papers",
                command=["missing"],
                sensitivity="public",
            )
        ]
    )
    monkeypatch.setattr(
        "tracefetch.unified.provider_availability",
        lambda *args, **kwargs: (False, "missing"),
    )
    with pytest.raises(SearchFailedError) as caught:
        search_everywhere(
            "query",
            scopes=["papers"],
            root=tmp_path,
            public_provider="all",
            limit=2,
            provider_manifest=manifest,
        )
    assert caught.value.details["attempts"][0]["status"] == "skipped"
