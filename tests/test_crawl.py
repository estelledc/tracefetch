from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracefetch.adapters.base import ReaderResult
from tracefetch.bundle import BundleResult, write_bundle
from tracefetch.config import Policy
from tracefetch.contracts import Attempt, LinkRecord
from tracefetch.crawl import CrawlStore, crawl_site, policy_sha256
from tracefetch.errors import InvalidInputError
from tracefetch.normalize import NormalizedDocument
from tracefetch.verify import crawl_verification_payload, verify_crawl_bundle

NOW = datetime(2026, 7, 24, tzinfo=UTC)
ROOT = "https://example.test/"


def policy(*, max_pages: int = 10, max_depth: int = 2) -> Policy:
    return Policy(
        deny_private_networks=False,
        min_interval_seconds=0,
        min_normalized_chars=1,
        max_pages=max_pages,
        max_depth=max_depth,
    )


def install_fake_fetch(
    monkeypatch: pytest.MonkeyPatch,
    links_by_url: dict[str, list[str]],
) -> list[str]:
    calls: list[str] = []

    def fake_fetch(
        url: str,
        output_dir: Path,
        *,
        reader: str,
        policy: Policy,
    ) -> BundleResult:
        del policy
        calls.append(url)
        document = NormalizedDocument(
            markdown=f"# Synthetic page\n\nSource: {url}\n",
            title="Synthetic page",
            links=[LinkRecord(text="next", url=link) for link in links_by_url.get(url, [])],
            warnings=[],
            method="test",
        )
        raw = ReaderResult(
            adapter=reader,
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body=f"<main>{url}</main>".encode(),
            fetched_at=NOW,
            robots_status="allowed",
        )
        attempt = Attempt(
            adapter=reader,
            status="success",
            started_at=NOW,
            completed_at=NOW,
            http_status=200,
        )
        return write_bundle(
            output_dir,
            raw,
            document,
            [attempt],
            Policy(min_normalized_chars=1),
            [reader],
        )

    monkeypatch.setattr("tracefetch.crawl.fetch_to_bundle", fake_fetch)
    return calls


def test_bounded_same_origin_crawl_writes_verifiable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = "https://example.test/child"
    calls = install_fake_fetch(
        monkeypatch,
        {
            ROOT: [child, "https://other.test/outside", "mailto:test@example.test"],
            child: [],
        },
    )
    output = tmp_path / "crawl"
    receipt = crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)

    assert calls == [ROOT, child]
    assert receipt.status == "complete"
    assert receipt.reader == "direct"
    assert receipt.policy_sha256 == policy_sha256(policy())
    assert [page.url for page in receipt.pages] == [ROOT, child]
    assert verify_crawl_bundle(output) == []
    payload = crawl_verification_payload(output)
    assert payload["valid"] is True
    assert payload["verified_pages"] == 2


def test_page_budget_stops_with_explicit_partial_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    children = [f"https://example.test/{index}" for index in range(3)]
    install_fake_fetch(monkeypatch, {ROOT: children, children[0]: []})
    receipt = crawl_site(
        ROOT,
        tmp_path / "crawl",
        reader="direct",
        policy=policy(max_pages=2),
        resume=False,
    )
    assert sum(page.status == "complete" for page in receipt.pages) == 2
    assert sum(page.status == "pending" for page in receipt.pages) == 2
    assert receipt.status == "partial"


def test_depth_zero_does_not_enqueue_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = install_fake_fetch(monkeypatch, {ROOT: ["https://example.test/child"]})
    receipt = crawl_site(
        ROOT,
        tmp_path / "crawl",
        reader="direct",
        policy=policy(max_depth=0),
        resume=False,
    )
    assert calls == [ROOT]
    assert len(receipt.pages) == 1


@pytest.mark.parametrize(
    ("root", "reader", "changed_policy", "message"),
    [
        ("https://changed.test/", "direct", policy(), "root URL"),
        (ROOT, "jina", policy(), "reader"),
        (ROOT, "direct", policy(max_depth=1), "policy"),
    ],
)
def test_resume_rejects_contract_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root: str,
    reader: str,
    changed_policy: Policy,
    message: str,
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    store = CrawlStore(output / "crawl.sqlite3")
    store.create_job(ROOT, "direct", policy())
    store.close()
    install_fake_fetch(monkeypatch, {})

    with pytest.raises(InvalidInputError, match=message):
        crawl_site(root, output, reader=reader, policy=changed_policy, resume=True)


def test_resume_recovers_running_page_and_reuses_valid_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    store = CrawlStore(output / "crawl.sqlite3")
    store.create_job(ROOT, "direct", policy())
    store.mark_running(ROOT)
    store.close()
    calls = install_fake_fetch(monkeypatch, {ROOT: []})

    first = crawl_site(ROOT, output, reader="direct", policy=policy(), resume=True)
    second_calls = install_fake_fetch(monkeypatch, {ROOT: []})
    second = crawl_site(ROOT, output, reader="direct", policy=policy(), resume=True)

    assert calls == [ROOT]
    assert second_calls == []
    assert first.status == second.status == "complete"
    assert verify_crawl_bundle(output) == []


def test_nonempty_new_output_is_rejected_without_modification(tmp_path: Path) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(InvalidInputError, match="not empty"):
        crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_crawl_receipt_path_escape_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "crawl"
    install_fake_fetch(monkeypatch, {ROOT: []})
    crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)
    receipt_path = output / "crawl-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["pages"][0]["bundle_path"] = "../outside"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    failures = verify_crawl_bundle(output)
    assert any("unsafe crawl bundle path" in failure for failure in failures)
    assert "crawl state page projection mismatch" in failures


def test_crawl_sqlite_tamper_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "crawl"
    install_fake_fetch(monkeypatch, {ROOT: []})
    crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)
    connection = sqlite3.connect(output / "crawl.sqlite3")
    connection.execute("update metadata set value = '99' where key = 'max_depth'")
    connection.commit()
    connection.close()
    failures = verify_crawl_bundle(output)
    assert "crawl state sha256 mismatch" in failures
    assert "crawl state metadata mismatch: max_depth" in failures


def test_failed_page_counts_are_recomputed_from_page_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "crawl"
    install_fake_fetch(monkeypatch, {ROOT: []})
    crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)
    receipt_path = output / "crawl-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["failures_by_code"] = {"fabricated": 1}
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert "failures_by_code does not match page records" in verify_crawl_bundle(output)
