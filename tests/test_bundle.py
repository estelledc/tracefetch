from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracefetch.adapters.base import ReaderResult
from tracefetch.bundle import write_bundle
from tracefetch.config import Policy
from tracefetch.contracts import Attempt, EvidenceReceipt
from tracefetch.errors import InvalidInputError
from tracefetch.normalize import normalize_local_file, normalize_result
from tracefetch.verify import verify_bundle


def reader_result(body: bytes) -> ReaderResult:
    return ReaderResult(
        adapter="direct",
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=body,
        fetched_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        robots_status="allowed",
    )


def success_attempt() -> Attempt:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    return Attempt(adapter="direct", status="success", started_at=now, completed_at=now)


def test_html_bundle_is_portable_and_verifiable(tmp_path: Path) -> None:
    body = b"""<!doctype html><html><head><title>Example</title></head><body>
    <main><h1>Evidence</h1><p>This is enough normalized evidence for a receipt.</p>
    <a href='/child'>Child</a><script>ignore()</script></main></body></html>"""
    result = reader_result(body)
    bundle = write_bundle(
        tmp_path / "bundle",
        result,
        normalize_result(result),
        [success_attempt()],
        Policy(min_normalized_chars=1),
        ["direct"],
    )

    assert verify_bundle(bundle.path) == []
    assert bundle.receipt.status == "complete"
    assert bundle.receipt.receipt_kind == "unsigned-self-reported-diagnostic"
    assert bundle.receipt.attested is False
    assert {item.path for item in bundle.receipt.artifacts} == {
        "raw.html",
        "normalized.md",
        "anchors.jsonl",
        "links.json",
    }
    assert all(not Path(item.path).is_absolute() for item in bundle.receipt.artifacts)
    assert "ignore()" not in (bundle.path / "normalized.md").read_text(encoding="utf-8")


def test_tampering_and_unexpected_entries_fail_verification(tmp_path: Path) -> None:
    result = reader_result(b"<main><h1>Title</h1><p>Body text.</p></main>")
    bundle = write_bundle(
        tmp_path / "bundle",
        result,
        normalize_result(result),
        [success_attempt()],
        Policy(min_normalized_chars=1),
        ["direct"],
    )
    (bundle.path / "raw.html").write_text("tampered", encoding="utf-8")
    (bundle.path / "surprise.txt").write_text("extra", encoding="utf-8")

    failures = verify_bundle(bundle.path)

    assert any("sha256 mismatch" in item for item in failures)
    assert "unexpected bundle entry: surprise.txt" in failures


def test_nonempty_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    marker = output / "owned.txt"
    marker.write_text("keep", encoding="utf-8")
    result = reader_result(b"<p>body</p>")

    with pytest.raises(InvalidInputError, match="not empty"):
        write_bundle(
            output,
            result,
            normalize_result(result),
            [success_attempt()],
            Policy(min_normalized_chars=1),
            ["direct"],
        )
    assert marker.read_text(encoding="utf-8") == "keep"


def test_invalid_local_json_is_preserved_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"unfinished":', encoding="utf-8")

    normalized = normalize_local_file(path)

    assert normalized.warnings == ["invalid_json_syntax"]
    assert '{"unfinished":' in normalized.markdown


def test_verifier_rejects_receipt_quality_tampering(tmp_path: Path) -> None:
    result = reader_result(b"<main><h1>Title</h1><p>Body text.</p></main>")
    bundle = write_bundle(
        tmp_path / "bundle",
        result,
        normalize_result(result),
        [success_attempt()],
        Policy(min_normalized_chars=1),
        ["direct"],
    )
    receipt_path = bundle.path / "receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["quality"]["link_count"] = 99
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    assert "link count mismatch" in verify_bundle(bundle.path)
    EvidenceReceipt.model_validate(payload)
