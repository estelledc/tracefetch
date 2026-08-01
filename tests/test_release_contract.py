from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_version_sources_and_release_tag_are_coherent() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/check_version.py", "--tag", "v1.0.1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "failures": [],
        "schema_version": "tracefetch.version-check.v1",
        "status": "ok",
        "version": "1.0.1",
    }


def test_release_workflow_builds_smokes_and_publishes_assets() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    release_docs = (ROOT / "docs/releasing.md").read_text(encoding="utf-8")

    assert 'tags:\n      - "v*.*.*"' in workflow
    assert "make check" in workflow
    assert 'scripts/check_version.py --tag "$GITHUB_REF_NAME"' in workflow
    assert "uv pip install" in workflow
    assert "gh release create" in workflow
    assert readme.count("v1.0.1") == 3
    assert "v1.0.0" not in readme
    assert "scripts/check_version.py --tag vMAJOR.MINOR.PATCH" in release_docs
    assert "PyPI publication is not claimed" in readme
