from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tracefetch.adapters.base import ReaderResult
from tracefetch.bundle import write_bundle
from tracefetch.config import Policy
from tracefetch.contracts import Attempt, LinkRecord
from tracefetch.errors import InvalidInputError
from tracefetch.normalize import NormalizedDocument
from tracefetch.pipeline import ingest_to_bundle
from tracefetch.verify import verification_payload, verify_bundle

FIXED_TIME = datetime(2026, 7, 24, 0, 0, tzinfo=UTC)


def build_bundle(root: Path, *, markdown: str | None = None) -> Path:
    text = markdown or (
        "# Evidence\n\n"
        "This synthetic document is deliberately long enough to pass the normalized content "
        "threshold. It contains no user data and exists only for deterministic contract tests.\n\n"
        "- first item\n- second item\n"
    )
    normalized = NormalizedDocument(
        markdown=text,
        title="Evidence",
        links=[LinkRecord(text="source", url="https://example.com/source")],
        warnings=[],
        method="test",
    )
    raw = ReaderResult(
        adapter="direct",
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/html; charset=utf-8",
        body=b"<main><h1>Evidence</h1><p>synthetic</p></main>",
        fetched_at=FIXED_TIME,
        robots_status="allowed",
    )
    attempt = Attempt(
        adapter="direct",
        status="success",
        started_at=FIXED_TIME,
        completed_at=FIXED_TIME,
        http_status=200,
    )
    return write_bundle(
        root,
        raw,
        normalized,
        [attempt],
        Policy(min_normalized_chars=40),
        ["direct"],
    ).path


def read_receipt(bundle: Path) -> dict[str, object]:
    return json.loads((bundle / "receipt.json").read_text(encoding="utf-8"))


def write_receipt(bundle: Path, payload: dict[str, object]) -> None:
    (bundle / "receipt.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_bundle_is_complete_explicitly_unsigned_and_verifiable(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    receipt = read_receipt(bundle)

    assert receipt["status"] == "complete"
    assert receipt["receipt_kind"] == "unsigned-self-reported-diagnostic"
    assert receipt["attested"] is False
    assert receipt["trusted_clock"] is False
    assert receipt["independent_execution_proven"] is False
    assert {item["role"] for item in receipt["artifacts"]} == {
        "raw",
        "normalized",
        "anchors",
        "links",
    }
    assert verify_bundle(bundle) == []
    assert verification_payload(bundle)["valid"] is True


@pytest.mark.parametrize(
    ("relative", "marker"),
    [
        ("raw.html", b"tamper"),
        ("normalized.md", b"\nchanged"),
        ("anchors.jsonl", b"{}\n"),
        ("links.json", b"[]\n"),
    ],
)
def test_artifact_tampering_is_rejected(tmp_path: Path, relative: str, marker: bytes) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    with (bundle / relative).open("ab") as stream:
        stream.write(marker)

    assert any("mismatch" in failure for failure in verify_bundle(bundle))


def test_missing_artifact_is_rejected(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    (bundle / "anchors.jsonl").unlink()
    assert any("missing artifact" in failure for failure in verify_bundle(bundle))


def test_unrecorded_file_is_rejected(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    (bundle / "injected.txt").write_text("untrusted", encoding="utf-8")
    assert "unexpected bundle entry: injected.txt" in verify_bundle(bundle)


def test_duplicate_artifact_role_is_rejected(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    receipt = read_receipt(bundle)
    artifacts = receipt["artifacts"]
    assert isinstance(artifacts, list)
    duplicate = dict(next(item for item in artifacts if item["role"] == "raw"))
    duplicate["path"] = "raw-copy.html"
    (bundle / "raw-copy.html").write_bytes((bundle / "raw.html").read_bytes())
    artifacts.append(duplicate)
    write_receipt(bundle, receipt)

    assert "duplicate artifact role: raw" in verify_bundle(bundle)


def test_missing_raw_role_is_rejected_even_if_manifest_hashes_are_valid(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    receipt = read_receipt(bundle)
    artifacts = receipt["artifacts"]
    assert isinstance(artifacts, list)
    receipt["artifacts"] = [item for item in artifacts if item["role"] != "raw"]
    (bundle / "raw.html").unlink()
    write_receipt(bundle, receipt)

    assert "bundle must contain exactly one raw artifact" in verify_bundle(bundle)


def test_source_hash_cannot_diverge_from_raw_artifact(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    receipt = read_receipt(bundle)
    source = receipt["source"]
    assert isinstance(source, dict)
    source["source_sha256"] = "0" * 64
    write_receipt(bundle, receipt)
    assert "source sha256 does not match raw artifact" in verify_bundle(bundle)


def test_quality_counts_are_recomputed(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    receipt = read_receipt(bundle)
    quality = receipt["quality"]
    assert isinstance(quality, dict)
    quality["normalized_chars"] = 1
    quality["anchor_count"] = 999
    quality["link_count"] = 999
    write_receipt(bundle, receipt)
    failures = verify_bundle(bundle)
    assert "normalized character count mismatch" in failures
    assert "anchor count mismatch" in failures
    assert "link count mismatch" in failures


def test_path_traversal_in_manifest_is_rejected(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    receipt = read_receipt(bundle)
    artifacts = receipt["artifacts"]
    assert isinstance(artifacts, list)
    artifacts[0]["path"] = "../outside.txt"
    write_receipt(bundle, receipt)
    assert "unsafe artifact path: ../outside.txt" in verify_bundle(bundle)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    receipt = read_receipt(bundle)
    artifacts = receipt["artifacts"]
    assert isinstance(artifacts, list)
    raw = next(item for item in artifacts if item["role"] == "raw")
    (bundle / str(raw["path"])).unlink()
    (bundle / str(raw["path"])).symlink_to(outside)
    assert any("unsafe artifact target" in failure for failure in verify_bundle(bundle))


def test_atomic_publication_leaves_no_partial_bundle_on_contract_failure(
    tmp_path: Path,
) -> None:
    output = tmp_path / "bundle"
    raw = ReaderResult(
        adapter="direct",
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=200,
        content_type="text/plain",
        body=b"raw",
        fetched_at=FIXED_TIME,
        robots_status="allowed",
    )
    attempt = Attempt(
        adapter="direct",
        status="success",
        started_at=FIXED_TIME,
        completed_at=FIXED_TIME,
    )
    with pytest.raises(ValidationError):
        write_bundle(output, raw, None, [attempt], Policy(), [], [])
    assert not output.exists()
    assert not list(tmp_path.glob(".bundle.*"))


def test_existing_nonempty_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(InvalidInputError, match="not empty"):
        build_bundle(output)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_invalid_local_json_is_preserved_with_warning(tmp_path: Path) -> None:
    source = tmp_path / "broken.json"
    source.write_text('{"unterminated":', encoding="utf-8")
    bundle = ingest_to_bundle(
        source,
        tmp_path / "bundle",
        source_url="https://example.com/broken.json",
        policy=Policy(min_normalized_chars=1),
    ).path
    receipt = read_receipt(bundle)
    assert "invalid_json_syntax" in receipt["warnings"]
    assert verify_bundle(bundle) == []
