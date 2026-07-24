from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracefetch.cli import main
from tracefetch.contracts import SearchAttempt, SearchCandidate, SearchEnvelope


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


def test_cli_search_plain_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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

    main(["search", "query"])

    assert "1. Candidate" in capsys.readouterr().out


@pytest.mark.parametrize(("raw", "message"), [("0", "greater"), ("-1", "greater")])
def test_cli_rejects_nonpositive_limits(
    raw: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["search", "query", "--limit", raw])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
