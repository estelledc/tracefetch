from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracefetch.cli import main
from tracefetch.contracts import (
    SearchResultAttempt,
    SearchResultCandidate,
    SearchResultsEnvelope,
)


def test_cli_ingest_and_verify_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Source\n\n" + "evidence " * 40, encoding="utf-8")
    output = tmp_path / "bundle"

    main(["ingest", str(source), "--output", str(output), "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "complete"
    assert receipt["attested"] is False

    main(["verify", str(output), "--json"])
    verification = json.loads(capsys.readouterr().out)
    assert verification["valid"] is True


def test_cli_verification_error_has_stable_json_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Source\n\n" + "evidence " * 40, encoding="utf-8")
    output = tmp_path / "bundle"
    main(["ingest", str(source), "--output", str(output), "--json"])
    capsys.readouterr()
    (output / "normalized.md").write_text("tampered", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["verify", str(output), "--json"])

    assert exit_info.value.code == 6
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "verification_failed"
    assert error["retryable"] is False


@pytest.mark.parametrize(
    ("contents", "failure"),
    [
        (b"{", "crawl receipt failed schema validation"),
        (b"{}", "crawl receipt failed schema validation"),
        (b"\xff", "crawl receipt is not valid UTF-8"),
    ],
)
def test_cli_crawl_receipt_load_errors_are_fixed_and_bounded(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    contents: bytes,
    failure: str,
) -> None:
    output = tmp_path / "crawl"
    output.mkdir()
    (output / "crawl-receipt.json").write_bytes(contents)

    with pytest.raises(SystemExit) as exit_info:
        main(["verify", str(output), "--json"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 6
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["schema_version"] == "tracefetch.error.v1"
    assert error["error"] == "verification_failed"
    assert error["details"]["failures"] == [failure]
    assert len(captured.err.encode("utf-8")) <= 4096
    assert "Traceback" not in captured.err
    assert "pydantic_core" not in captured.err
    assert "ValidationError" not in captured.err


def test_cli_large_invalid_crawl_receipt_does_not_reflect_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel = "TOP_SECRET_SENTINEL"
    output = tmp_path / "crawl"
    output.mkdir()
    pages = [{"url": f"https://example.test/{index}/{sentinel}"} for index in range(5000)]
    (output / "crawl-receipt.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["verify", str(output), "--json"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 6
    assert captured.out == ""
    assert len(captured.err.encode("utf-8")) <= 4096
    assert sentinel not in captured.err
    assert json.loads(captured.err)["details"]["failures"] == [
        "crawl receipt failed schema validation"
    ]


def test_cli_schema_and_doctor_emit_machine_readable_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["schema", "evidence"])
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "EvidenceReceipt"

    main(["doctor", "--json"])
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["schema_version"] == "tracefetch.doctor.v1"
    assert any(check["name"] == "python" for check in doctor["checks"])


def test_cli_search_defaults_to_json_and_pretty_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    envelope = SearchResultsEnvelope(
        query="query",
        scopes=["public"],
        sensitivity="public-or-project",
        candidates=[
            SearchResultCandidate(
                rank=1,
                title="Candidate",
                locator="https://example.com/",
                provider="fixture",
                scope="public",
                source_class="web-candidate",
                evidence_state="candidate-only",
                sensitivity="public",
            )
        ],
        attempts=[SearchResultAttempt(provider="fixture", scope="public", status="success")],
    )
    monkeypatch.setattr("tracefetch.cli.search_everywhere", lambda *args, **kwargs: envelope)

    main(["search", "query"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "tracefetch.search-results.v1"

    main(["search", "query", "--format", "pretty"])
    assert "1. [public/fixture] Candidate" in capsys.readouterr().out


def test_cli_keeps_explicit_provider_as_legacy_compatibility_mode(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tracefetch.contracts import SearchAttempt, SearchCandidate, SearchEnvelope

    envelope = SearchEnvelope(
        query="query",
        candidates=[
            SearchCandidate(
                rank=1,
                title="Candidate",
                url="https://example.com/",
                provider="fixture",
            )
        ],
        attempts=[SearchAttempt(provider="fixture", status="success")],
    )
    monkeypatch.setattr("tracefetch.cli.search_sources", lambda *args, **kwargs: envelope)

    main(["search", "query", "--provider", "all", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "tracefetch.search.v1"


@pytest.mark.parametrize(("raw", "message"), [("0", "greater"), ("-1", "greater")])
def test_cli_rejects_nonpositive_limits(
    raw: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["search", "query", "--limit", raw])
    assert exit_info.value.code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "invalid_input"
    assert message in error["message"]
