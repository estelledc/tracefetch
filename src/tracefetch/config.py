from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tracefetch.errors import InvalidInputError


class Policy(BaseModel):
    """Fail-closed defaults for public-source acquisition."""

    model_config = ConfigDict(extra="forbid")

    user_agent: str = "TraceFetch/1.0"
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    max_bytes: int = Field(default=5_000_000, ge=1_024, le=50_000_000)
    max_redirects: int = Field(default=5, ge=0, le=10)
    obey_robots: bool = True
    robots_fail_closed: bool = True
    deny_private_networks: bool = True
    require_https: bool = False
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    allow_subdomains: bool = True
    allow_remote_adapters: bool = False
    allow_authenticated_adapters: bool = False
    min_interval_seconds: float = Field(default=1.0, ge=0, le=60)
    min_normalized_chars: int = Field(default=160, ge=1, le=10_000)
    max_pages: int = Field(default=20, ge=1, le=1_000)
    max_depth: int = Field(default=1, ge=0, le=10)

    @field_validator("allowed_domains", "denied_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = value.strip().lower().lstrip(".").rstrip(".")
            if not domain or "/" in domain or ":" in domain:
                raise ValueError(f"invalid domain policy entry: {value!r}")
            normalized.append(domain)
        return sorted(set(normalized))


def load_policy(path: str | Path | None) -> Policy:
    if path is None:
        return Policy()
    policy_path = Path(path)
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
        return Policy.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise InvalidInputError(f"invalid policy file {policy_path}: {exc}") from exc
