from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracefetch.adapters.base import ReaderResult
from tracefetch.adapters.readers import DirectReader, JinaReader
from tracefetch.config import Policy
from tracefetch.errors import FetchFailedError
from tracefetch.pipeline import fetch_to_bundle, ingest_to_bundle
from tracefetch.verify import verify_bundle


def result(adapter: str, body: str, *, provided: bool = False) -> ReaderResult:
    return ReaderResult(
        adapter=adapter,
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/markdown" if provided else "text/html",
        body=body.encode(),
        fetched_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        robots_status="allowed",
        provided_markdown=body if provided else None,
        metadata={"raw_origin_preserved": not provided},
    )


def test_auto_fallback_keeps_direct_raw_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    direct = result("direct", "<p>thin</p>")
    remote_markdown = "# Better\n\n" + "evidence " * 40
    remote = result("jina", remote_markdown, provided=True)
    monkeypatch.setattr(DirectReader, "fetch", lambda self, url, policy: direct)
    monkeypatch.setattr(JinaReader, "fetch", lambda self, url, policy: remote)

    bundle = fetch_to_bundle(
        "https://example.com",
        tmp_path / "bundle",
        reader="auto",
        policy=Policy(
            allow_remote_adapters=True,
            min_normalized_chars=100,
            deny_private_networks=False,
            obey_robots=False,
        ),
    )

    assert (bundle.path / "raw.html").read_bytes() == direct.body
    assert "Better" in (bundle.path / "normalized.md").read_text(encoding="utf-8")
    assert bundle.receipt.route == ["direct", "jina"]
    assert bundle.receipt.policy.remote_adapter_used is True
    assert verify_bundle(bundle.path) == []


def test_failed_remote_attempt_is_still_disclosed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    direct = result("direct", "<p>thin</p>")
    monkeypatch.setattr(DirectReader, "fetch", lambda self, url, policy: direct)
    monkeypatch.setattr(
        JinaReader,
        "fetch",
        lambda self, url, policy: (_ for _ in ()).throw(FetchFailedError("remote down")),
    )

    bundle = fetch_to_bundle(
        "https://example.com",
        tmp_path / "bundle",
        reader="auto",
        policy=Policy(
            allow_remote_adapters=True,
            min_normalized_chars=100,
            deny_private_networks=False,
            obey_robots=False,
        ),
    )

    assert bundle.receipt.route == ["direct", "jina"]
    assert bundle.receipt.policy.remote_adapter_used is True
    assert any(
        item.adapter == "jina" and item.status == "failed" for item in bundle.receipt.attempts
    )


def test_local_ingest_resolves_links_against_claimed_source(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n\n[Child](/child)\n", encoding="utf-8")
    bundle = ingest_to_bundle(
        source,
        tmp_path / "bundle",
        source_url="https://example.com/docs/note",
        policy=Policy(min_normalized_chars=1),
    )

    assert bundle.receipt.source.requested_url == "https://example.com/docs/note"
    links = (bundle.path / "links.json").read_text(encoding="utf-8")
    assert "https://example.com/child" in links


def test_programmatic_reader_adapter_is_audited_as_remote(tmp_path: Path) -> None:
    class FixtureReader:
        name = "fixture"
        remote = True
        authenticated = False

        def available(self) -> tuple[bool, str]:
            return True, "fixture"

        def fetch(self, url: str, policy: Policy) -> ReaderResult:
            return result("fixture", "# Fixture\n\n" + "evidence " * 30, provided=True)

    bundle = fetch_to_bundle(
        "https://example.com",
        tmp_path / "bundle",
        reader="fixture",
        policy=Policy(min_normalized_chars=1),
        adapters={"fixture": FixtureReader()},
    )

    assert bundle.receipt.route == ["fixture"]
    assert bundle.receipt.policy.remote_adapter_used is True
    assert verify_bundle(bundle.path) == []
