from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracefetch.adapters.base import ReaderResult
from tracefetch.adapters.readers import FirecrawlReader, JinaReader
from tracefetch.adapters.search import ExaSearchProvider, GitHubSearchProvider, _parse_exa_output
from tracefetch.anchors import build_anchors
from tracefetch.cli import build_parser, dispatch, main
from tracefetch.config import Policy, load_policy
from tracefetch.contracts import SearchCandidate
from tracefetch.doctor import run_doctor
from tracefetch.errors import AdapterUnavailableError, InvalidInputError, PolicyBlockedError
from tracefetch.normalize import normalize_local_file, normalize_result
from tracefetch.pipeline import _reader_route
from tracefetch.search import search_sources

NOW = datetime(2026, 7, 24, tzinfo=UTC)


def result(
    body: bytes,
    content_type: str,
    *,
    provided_markdown: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ReaderResult:
    return ReaderResult(
        adapter="direct",
        requested_url="https://example.com/base/",
        final_url="https://example.com/base/",
        status_code=200,
        content_type=content_type,
        body=body,
        fetched_at=NOW,
        robots_status="allowed",
        provided_markdown=provided_markdown,
        metadata=metadata or {},
    )


def test_html_normalization_removes_active_content_and_resolves_links() -> None:
    document = normalize_result(
        result(
            b"""
            <html><head><title>Example</title><script>secret()</script></head>
            <body><main><h1>Heading</h1><p>Readable text.</p>
            <a href="../source">Source</a></main></body></html>
            """,
            "text/html; charset=utf-8",
        )
    )
    assert document.title == "Example"
    assert "secret" not in document.markdown
    assert document.links[0].url == "https://example.com/source"
    assert document.method == "builtin:html"


def test_html_normalization_removes_embedded_xml_permalink_noise() -> None:
    document = normalize_result(
        result(
            b"""
            <html><body><main>
            <h2 id="overview">Overview
              <a title="Permalink for Overview section" href="#overview">
                <?xml version="1.0" encoding="utf-8"?>
                <svg aria-hidden="true"><path d="M0 0"/></svg>
              </a>
            </h2>
            <p>Readable documentation content that is deliberately long enough
            for main-content selection to retain this fixture as the candidate.</p>
            </main></body></html>
            """,
            "text/html; charset=utf-8",
        )
    )

    assert "## Overview" in document.markdown
    assert "xml version" not in document.markdown
    assert "Permalink for Overview" not in document.markdown
    assert document.links == []


def test_provided_markdown_records_when_origin_bytes_are_not_preserved() -> None:
    document = normalize_result(
        result(
            b"remote",
            "text/markdown",
            provided_markdown="# Remote\n\nContent",
            metadata={"raw_origin_preserved": False},
        )
    )
    assert document.warnings == ["raw_origin_not_preserved"]
    assert document.method == "direct:provided-markdown"


def test_json_normalization_is_sorted_and_fenced() -> None:
    document = normalize_result(result(b'{"b":2,"a":1}', "application/json"))
    assert document.markdown.startswith("```json\n")
    assert document.markdown.index('"a"') < document.markdown.index('"b"')


def test_local_text_can_resolve_links_against_explicit_source_url(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n\n[child](child.html)\n", encoding="utf-8")
    document = normalize_local_file(source, base_url="https://example.com/root/")
    assert document.links[0].url == "https://example.com/root/child.html"


def test_binary_normalization_requires_explicit_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"not-a-pdf")
    monkeypatch.setattr("tracefetch.normalize.shutil.which", lambda _name: None)
    with pytest.raises(AdapterUnavailableError, match="MarkItDown"):
        normalize_local_file(source)


def test_anchor_builder_covers_heading_list_table_code_and_paragraph() -> None:
    markdown = """# Heading

Paragraph text.

- one
- two

| a | b |
|---|---|
| 1 | 2 |

```python
print('ok')
```
"""
    anchors = build_anchors(markdown)
    assert {anchor.kind for anchor in anchors} == {
        "heading",
        "paragraph",
        "list",
        "table",
        "code",
    }
    assert all(anchor.line_start <= anchor.line_end for anchor in anchors)


def test_auto_reader_route_requires_explicit_remote_opt_in() -> None:
    assert _reader_route("auto", Policy()) == ["direct"]
    assert _reader_route("auto", Policy(allow_remote_adapters=True)) == ["direct", "jina"]
    assert _reader_route(
        "auto",
        Policy(allow_remote_adapters=True, allow_authenticated_adapters=True),
    ) == ["direct", "jina", "firecrawl"]


def test_remote_readers_fail_before_network_without_policy_opt_in() -> None:
    with pytest.raises(PolicyBlockedError, match="remote adapters"):
        JinaReader().fetch("https://example.com/", Policy())
    with pytest.raises(PolicyBlockedError, match="remote adapters"):
        FirecrawlReader().fetch("https://example.com/", Policy())


def test_policy_loader_rejects_unknown_fields(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text('{"unknown": true}', encoding="utf-8")
    with pytest.raises(InvalidInputError, match="invalid policy"):
        load_policy(policy)


@pytest.mark.parametrize(("query", "limit"), [("   ", 1), ("ok", 0)])
def test_search_contract_rejects_empty_query_or_nonpositive_limit(query: str, limit: int) -> None:
    with pytest.raises(InvalidInputError):
        search_sources(query, provider="auto", limit=limit)


def test_search_all_deduplicates_and_reranks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ExaSearchProvider, "available", lambda _self: (True, "test"))
    monkeypatch.setattr(GitHubSearchProvider, "available", lambda _self: (True, "test"))
    monkeypatch.setattr(
        ExaSearchProvider,
        "search",
        lambda _self, _query, _limit: [
            SearchCandidate(rank=8, title="A", url="https://example.com/a", provider="exa")
        ],
    )
    monkeypatch.setattr(
        GitHubSearchProvider,
        "search",
        lambda _self, _query, _limit: [
            SearchCandidate(rank=9, title="A2", url="https://example.com/a", provider="github"),
            SearchCandidate(rank=10, title="B", url="https://example.com/b", provider="github"),
        ],
    )
    envelope = search_sources(" evidence ", provider="all", limit=3)
    assert [candidate.url for candidate in envelope.candidates] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert [candidate.rank for candidate in envelope.candidates] == [1, 2]


def test_exa_text_parser_has_bounded_fallback() -> None:
    candidates = _parse_exa_output(
        "Title: One\nURL: https://example.com/one\nPublished: N/A\n"
        "Highlights: first\n---\nTitle: Two\nURL: https://example.com/two\n",
        1,
    )
    assert len(candidates) == 1
    assert candidates[0].title == "One"
    assert candidates[0].published_at is None


def test_schema_command_emits_machine_readable_contract(capsys: pytest.CaptureFixture[str]) -> None:
    args = build_parser().parse_args(["schema", "evidence"])
    assert dispatch(args) is None
    payload = json.loads(capsys.readouterr().out)
    assert payload["title"] == "EvidenceReceipt"
    assert "receipt_kind" in payload["properties"]


def test_cli_error_is_stable_json_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["search", " ", "--json"])
    assert captured.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"] == "invalid_input"
    assert payload["retryable"] is False


def test_doctor_never_returns_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "secret-value-must-not-leak")
    serialized = run_doctor().model_dump_json()
    assert "secret-value-must-not-leak" not in serialized
    assert "FIRECRAWL_API_KEY configured" in serialized
