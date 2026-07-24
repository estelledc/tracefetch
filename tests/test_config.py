from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracefetch.config import Policy, load_policy
from tracefetch.errors import InvalidInputError


def test_policy_domains_are_normalized_and_deduplicated() -> None:
    policy = Policy(allowed_domains=[".Example.COM.", "example.com"])
    assert policy.allowed_domains == ["example.com"]


def test_load_policy_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"unknown": True}), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="invalid policy"):
        load_policy(path)


def test_load_policy_reads_strict_json(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps({"allowed_domains": ["example.com"], "max_pages": 3}),
        encoding="utf-8",
    )
    policy = load_policy(path)
    assert policy.allowed_domains == ["example.com"]
    assert policy.max_pages == 3
