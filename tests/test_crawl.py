from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import tracefetch.verify as verify_module
from tracefetch.adapters.base import ReaderResult
from tracefetch.bundle import BundleResult, write_bundle
from tracefetch.cli import main
from tracefetch.config import Policy
from tracefetch.contracts import Attempt, LinkRecord
from tracefetch.crawl import CrawlStore, crawl_site, policy_sha256
from tracefetch.errors import InvalidInputError
from tracefetch.normalize import NormalizedDocument
from tracefetch.verify import (
    MAX_CRAWL_RECEIPT_BYTES,
    crawl_verification_payload,
    verify_crawl_bundle,
)

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
            ROOT: [child, "https://other.test/outside", "mailto:fixture"],
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


@pytest.mark.parametrize(
    ("contents", "failure"),
    [
        (b"{", "crawl receipt failed schema validation"),
        (b"{}", "crawl receipt failed schema validation"),
        (b"\xff", "crawl receipt is not valid UTF-8"),
    ],
)
def test_crawl_payload_keeps_receipt_load_failures_inside_the_contract(
    tmp_path: Path, contents: bytes, failure: str
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(contents)

    assert crawl_verification_payload(output) == {
        "schema_version": "tracefetch.crawl-verification.v1",
        "crawl_id": "",
        "valid": False,
        "verified_pages": 0,
        "failures": [failure],
    }


def test_crawl_receipt_symlink_is_a_fixed_unreadable_failure(tmp_path: Path) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    outside = tmp_path / "outside-receipt.json"
    outside.write_text("{}", encoding="utf-8")
    (output / "crawl-receipt.json").symlink_to(outside)

    assert crawl_verification_payload(output)["failures"] == ["crawl receipt is unreadable"]


def test_oversized_crawl_receipt_is_a_fixed_bounded_failure(tmp_path: Path) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(b"x" * (MAX_CRAWL_RECEIPT_BYTES + 1))

    assert crawl_verification_payload(output) == {
        "schema_version": "tracefetch.crawl-verification.v1",
        "crawl_id": "",
        "valid": False,
        "verified_pages": 0,
        "failures": ["crawl receipt exceeds the size limit"],
    }


def test_crawl_receipt_at_the_size_limit_is_not_rejected_as_oversized(tmp_path: Path) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(b"x" * MAX_CRAWL_RECEIPT_BYTES)

    assert crawl_verification_payload(output)["failures"] == [
        "crawl receipt failed schema validation"
    ]


def test_crawl_receipt_truncated_mid_read_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(b"x" * 70000)
    real = verify_module._read_receipt_chunk
    calls = 0

    def fake(descriptor: int, size: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls >= 2:
            return b""
        return real(descriptor, size)

    monkeypatch.setattr(verify_module, "_read_receipt_chunk", fake)

    assert crawl_verification_payload(output)["failures"] == [
        "crawl receipt changed during verification"
    ]


def test_crawl_receipt_short_read_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(b"{}")
    real = verify_module._read_receipt_chunk

    def fake(descriptor: int, size: int) -> bytes:
        return real(descriptor, size - 1)

    monkeypatch.setattr(verify_module, "_read_receipt_chunk", fake)

    assert crawl_verification_payload(output)["failures"] == [
        "crawl receipt changed during verification"
    ]


def test_crawl_receipt_read_error_is_a_fixed_unreadable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(b"{}")

    def fake(descriptor: int, size: int) -> bytes:
        raise OSError("injected read failure")

    monkeypatch.setattr(verify_module, "_read_receipt_chunk", fake)

    assert crawl_verification_payload(output)["failures"] == ["crawl receipt is unreadable"]


def test_crawl_receipt_fstat_error_is_a_fixed_unreadable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(b"{}")

    def fake(descriptor: int) -> os.stat_result:
        raise OSError("injected fstat failure")

    monkeypatch.setattr(verify_module.os, "fstat", fake)

    assert crawl_verification_payload(output)["failures"] == ["crawl receipt is unreadable"]


@pytest.mark.parametrize("missing_flag", ["O_NOFOLLOW", "O_NONBLOCK"])
def test_crawl_receipt_requires_safe_open_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_flag: str,
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(b"{}")
    available = {
        "O_NOFOLLOW": os.O_NOFOLLOW,
        "O_NONBLOCK": os.O_NONBLOCK,
        "O_RDONLY": os.O_RDONLY,
    }
    del available[missing_flag]
    monkeypatch.setattr(verify_module, "os", SimpleNamespace(**available))

    assert crawl_verification_payload(output)["failures"] == ["crawl receipt is unreadable"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
def test_crawl_receipt_fifo_fails_without_blocking(tmp_path: Path) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    os.mkfifo(output / "crawl-receipt.json")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys;"
                "from pathlib import Path;"
                "from tracefetch.verify import crawl_verification_payload;"
                "print(json.dumps(crawl_verification_payload(Path(sys.argv[1]))))"
            ),
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["failures"] == ["crawl receipt is unreadable"]
    assert completed.stderr == ""
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("state_kind", ["missing", "corrupt", "symlink"])
def test_valid_receipt_summary_survives_invalid_crawl_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_kind: str,
) -> None:
    output = tmp_path / "crawl"
    install_fake_fetch(monkeypatch, {ROOT: []})
    receipt = crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)
    state_path = output / "crawl.sqlite3"
    if state_kind == "missing":
        state_path.unlink()
    elif state_kind == "corrupt":
        state_path.write_bytes(b"not a SQLite database")
    else:
        outside = tmp_path / "outside.sqlite3"
        outside.write_bytes(state_path.read_bytes())
        state_path.unlink()
        state_path.symlink_to(outside)

    payload = crawl_verification_payload(output)

    assert payload["valid"] is False
    assert payload["crawl_id"] == receipt.crawl_id
    assert payload["verified_pages"] == 1
    if state_kind == "symlink":
        assert payload["failures"] == ["crawl receipt and state must not be symlinks"]


def test_invalid_sqlite_row_is_fixed_for_payload_and_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "crawl"
    install_fake_fetch(monkeypatch, {ROOT: []})
    receipt = crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)
    connection = sqlite3.connect(output / "crawl.sqlite3")
    connection.execute("update pages set depth = 'not-an-integer'")
    connection.commit()
    connection.close()

    payload = crawl_verification_payload(output)
    assert payload["crawl_id"] == receipt.crawl_id
    assert payload["verified_pages"] == 1
    assert payload["failures"][-1] == "crawl SQLite state is invalid"

    with pytest.raises(SystemExit) as exit_info:
        main(["verify", str(output), "--json"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 6
    assert captured.out == ""
    assert json.loads(captured.err)["details"]["failures"][-1] == "crawl SQLite state is invalid"
    assert "Traceback" not in captured.err


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


def test_crawl_status_is_recomputed_from_page_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "crawl"
    install_fake_fetch(monkeypatch, {ROOT: []})
    receipt = crawl_site(ROOT, output, reader="direct", policy=policy(), resume=False)
    assert receipt.status == "complete"
    receipt_path = output / "crawl-receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    assert verify_crawl_bundle(output) == ["crawl status does not match page records"]
