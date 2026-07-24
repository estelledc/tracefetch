from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tracefetch.errors import InvalidInputError
from tracefetch.workspace import search_workspace


def test_workspace_python_fallback_ranks_exact_phrase_and_skips_generated_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "README.md").write_text(
        "# Product\n\nAgent search protocol is stable.\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "other.md").write_text(
        "Agent notes live here.\nThe protocol is described elsewhere.\n",
        encoding="utf-8",
    )
    noise = tmp_path / "node_modules"
    noise.mkdir()
    (noise / "noise.md").write_text("Agent search protocol", encoding="utf-8")
    monkeypatch.setattr("tracefetch.workspace.shutil.which", lambda command: None)

    candidates, attempt, warnings = search_workspace(
        tmp_path,
        "Agent search protocol",
        limit=5,
    )

    assert attempt.backend == "python-fallback"
    assert candidates[0].locator == "README.md:3"
    assert candidates[0].metadata["query_relaxed"] is False
    assert all("node_modules" not in item.locator for item in candidates)
    assert "ignore handling is limited" in warnings[0]


def test_workspace_python_fallback_uses_relaxed_terms_and_git_file_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "guide.md"
    source.write_text(
        "Agent routing details.\nEvidence remains separate.\n",
        encoding="utf-8",
    )

    def which(command: str) -> str | None:
        return "/usr/bin/git" if command == "git" else None

    monkeypatch.setattr("tracefetch.workspace.shutil.which", which)
    monkeypatch.setattr(
        "tracefetch.workspace._run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="guide.md\0", stderr=""
        ),
    )

    candidates, attempt, warnings = search_workspace(
        tmp_path,
        "Agent evidence",
        limit=3,
    )

    assert warnings == []
    assert attempt.metadata["query_relaxed"] is True
    assert candidates[0].metadata["matched_terms"] == ["Agent", "evidence"]
    assert candidates[0].metadata["term_coverage"] == 1.0


def test_workspace_parses_ripgrep_json_and_falls_back_after_rg_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text("Unified search contract\n", encoding="utf-8")
    payload = {
        "type": "match",
        "data": {
            "path": {"text": "docs/guide.md"},
            "lines": {"text": "Unified search contract\n"},
            "line_number": 1,
        },
    }
    monkeypatch.setattr("tracefetch.workspace.shutil.which", lambda command: "/fake/rg")
    monkeypatch.setattr(
        "tracefetch.workspace._run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=json.dumps(payload), stderr=""
        ),
    )

    candidates, attempt, warnings = search_workspace(
        tmp_path,
        "Unified search contract",
        limit=2,
    )

    assert warnings == []
    assert attempt.backend == "ripgrep"
    assert candidates[0].locator == "docs/guide.md:1"
    assert candidates[0].metadata["relevance_score"] >= 100

    def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 2, stdout="", stderr="failed")

    monkeypatch.setattr("tracefetch.workspace._run", failing_run)
    fallback, fallback_attempt, fallback_warnings = search_workspace(
        tmp_path,
        "Unified search contract",
        limit=2,
    )
    assert fallback[0].locator == "docs/guide.md:1"
    assert fallback_attempt.backend == "python-fallback"
    assert "ripgrep failed" in fallback_warnings[0]


def test_workspace_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match="existing directory"):
        search_workspace(tmp_path / "missing", "query", limit=2)
