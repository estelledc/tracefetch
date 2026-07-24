from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from tracefetch.contracts import CommandProviderSpec
from tracefetch.errors import (
    AdapterUnavailableError,
    InvalidInputError,
    ProviderProtocolError,
    SearchFailedError,
)
from tracefetch.providers import (
    load_provider_manifest,
    provider_availability,
    run_command_provider,
)


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _manifest(path: Path, providers: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"schema_version": "tracefetch.providers.v1", "providers": providers}),
        encoding="utf-8",
    )
    return path


def test_command_provider_round_trip_bounds_content_and_removes_secret_metadata(
    tmp_path: Path,
) -> None:
    executable = _executable(
        tmp_path / "provider.py",
        """#!/usr/bin/env python3
import json, sys
request = json.load(sys.stdin)
print(json.dumps({
  'schema_version': 'tracefetch.provider-response.v1',
  'provider': 'papers',
  'scope': 'papers',
  'candidates': [{
    'title': 'Paper ' + request['query'],
    'locator': 'https://example.com/paper',
    'snippet': 'abstract ' * 400,
    'metadata': {'citations': 12, 'api_token': 'must-not-escape'}
  }],
  'warnings': ['candidate metadata only']
}))
""",
    )
    spec = CommandProviderSpec(
        name="papers",
        scope="papers",
        command=[str(executable)],
        sensitivity="public",
        source_class="paper-candidate",
    )

    response = run_command_provider(
        spec,
        query="actor isolation",
        limit=3,
        base_dir=tmp_path,
    )

    assert response.candidates[0].title == "Paper actor isolation"
    assert response.candidates[0].snippet.endswith("[truncated]")
    assert response.candidates[0].metadata == {"citations": 12}
    assert response.warnings == ["candidate metadata only"]


def test_provider_manifest_enforces_identity_isolation_and_reserved_names(
    tmp_path: Path,
) -> None:
    valid_path = _manifest(
        tmp_path / "valid.json",
        [
            {
                "name": "lark",
                "scope": "internal",
                "command": ["provider"],
                "sensitivity": "internal",
                "isolated": True,
            }
        ],
    )
    manifest, base_dir = load_provider_manifest(valid_path)
    assert manifest.providers[0].name == "lark"
    assert base_dir == tmp_path

    duplicate = _manifest(
        tmp_path / "duplicate.json",
        [
            {
                "name": "papers",
                "scope": "papers",
                "command": ["one"],
                "sensitivity": "public",
            },
            {
                "name": "papers",
                "scope": "research",
                "command": ["two"],
                "sensitivity": "public",
            },
        ],
    )
    with pytest.raises(InvalidInputError, match="duplicate"):
        load_provider_manifest(duplicate)

    unisolated = _manifest(
        tmp_path / "unisolated.json",
        [
            {
                "name": "lark",
                "scope": "internal",
                "command": ["provider"],
                "sensitivity": "internal",
            }
        ],
    )
    with pytest.raises(InvalidInputError, match="isolated=true"):
        load_provider_manifest(unisolated)

    reserved = _manifest(
        tmp_path / "reserved.json",
        [
            {
                "name": "github",
                "scope": "papers",
                "command": ["provider"],
                "sensitivity": "public",
            }
        ],
    )
    with pytest.raises(InvalidInputError, match="reserved"):
        load_provider_manifest(reserved)


def test_provider_failures_are_stable_redacted_and_bounded(tmp_path: Path) -> None:
    failed = _executable(
        tmp_path / "failed.py",
        """#!/usr/bin/env python3
import sys
print('Authorization: Bearer private-value ' + ('detail ' * 100), file=sys.stderr)
raise SystemExit(9)
""",
    )
    spec = CommandProviderSpec(
        name="failed",
        scope="papers",
        command=[str(failed)],
        sensitivity="public",
    )

    with pytest.raises(SearchFailedError) as caught:
        run_command_provider(spec, query="query", limit=2, base_dir=tmp_path)

    diagnostic = str(caught.value.details["diagnostic"])
    assert "private-value" not in diagnostic
    assert "[redacted]" in diagnostic
    assert len(diagnostic) <= 320

    invalid = _executable(
        tmp_path / "invalid.py",
        "#!/usr/bin/env python3\nprint('not json')\n",
    )
    invalid_spec = spec.model_copy(update={"name": "invalid", "command": [str(invalid)]})
    with pytest.raises(ProviderProtocolError, match="invalid response"):
        run_command_provider(invalid_spec, query="query", limit=2, base_dir=tmp_path)

    oversized = _executable(
        tmp_path / "oversized.py",
        "#!/usr/bin/env python3\nprint('x' * 2000001)\n",
    )
    oversized_spec = spec.model_copy(update={"name": "oversized", "command": [str(oversized)]})
    with pytest.raises(ProviderProtocolError, match="2 MB"):
        run_command_provider(oversized_spec, query="query", limit=2, base_dir=tmp_path)


def test_provider_resolution_reports_unavailable_and_relative_executable(
    tmp_path: Path,
) -> None:
    missing = CommandProviderSpec(
        name="missing",
        scope="papers",
        command=["./missing"],
        sensitivity="public",
    )
    assert provider_availability(missing, base_dir=tmp_path)[0] is False
    with pytest.raises(AdapterUnavailableError):
        run_command_provider(missing, query="query", limit=1, base_dir=tmp_path)

    relative = _executable(
        tmp_path / "relative.py",
        """#!/usr/bin/env python3
import json
print(json.dumps({'schema_version': 'tracefetch.provider-response.v1',
                  'provider': 'relative', 'scope': 'papers', 'candidates': []}))
""",
    )
    spec = missing.model_copy(update={"name": "relative", "command": [f"./{relative.name}"]})
    available, backend = provider_availability(spec, base_dir=tmp_path)
    assert available is True
    assert backend == "relative.py"
    assert run_command_provider(spec, query="query", limit=1, base_dir=tmp_path).candidates == []
