from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Attempt(StrictModel):
    adapter: str
    status: Literal["success", "failed", "skipped"]
    started_at: datetime
    completed_at: datetime
    code: str | None = None
    message: str | None = None
    retryable: bool = False
    http_status: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRef(StrictModel):
    role: Literal["raw", "normalized", "anchors", "links", "crawl_state"]
    path: str
    media_type: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0)


class SourceRecord(StrictModel):
    requested_url: str
    normalized_url: str
    final_url: str
    fetched_at: datetime
    status_code: int | None = None
    content_type: str
    bytes: int = Field(ge=0)
    title: str | None = None
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PolicyRecord(StrictModel):
    robots_status: Literal["allowed", "blocked", "unavailable", "unchecked"]
    private_networks_denied: bool
    remote_adapter_used: bool
    authenticated_adapter_used: bool
    license_status: Literal["needs_review", "allowed", "restricted", "rejected"] = "needs_review"
    terms_status: Literal["not_assessed", "reviewed"] = "not_assessed"
    warnings: list[str] = Field(default_factory=list)


class QualityRecord(StrictModel):
    normalized_chars: int = Field(ge=0)
    anchor_count: int = Field(ge=0)
    link_count: int = Field(ge=0)
    truncated: bool = False
    thin_content: bool = False


class EvidenceReceipt(StrictModel):
    schema_version: Literal["tracefetch.evidence-receipt.v1"] = "tracefetch.evidence-receipt.v1"
    receipt_kind: Literal["unsigned-self-reported-diagnostic"] = "unsigned-self-reported-diagnostic"
    attested: Literal[False] = False
    trusted_clock: Literal[False] = False
    independent_execution_proven: Literal[False] = False
    receipt_id: str = Field(pattern=r"^tf_[a-f0-9]{16}_[0-9]{8}T[0-9]{6}Z$")
    created_at: datetime = Field(default_factory=utc_now)
    status: Literal["complete", "partial", "failed"]
    source: SourceRecord
    route: list[str] = Field(min_length=1)
    attempts: list[Attempt] = Field(min_length=1)
    artifacts: list[ArtifactRef] = Field(min_length=1)
    policy: PolicyRecord
    quality: QualityRecord
    warnings: list[str] = Field(default_factory=list)


class AnchorRecord(StrictModel):
    anchor_id: str = Field(pattern=r"^a_[a-f0-9]{12}$")
    kind: Literal["heading", "paragraph", "code", "table", "list"]
    heading: str | None = None
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview: str


class LinkRecord(StrictModel):
    text: str
    url: str
    source_anchor: str | None = None


class SearchCandidate(StrictModel):
    rank: int = Field(ge=1)
    title: str
    url: str
    snippet: str = ""
    provider: str
    published_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchAttempt(StrictModel):
    provider: str
    status: Literal["success", "failed", "skipped"]
    code: str | None = None
    message: str | None = None


class SearchEnvelope(StrictModel):
    schema_version: Literal["tracefetch.search.v1"] = "tracefetch.search.v1"
    query: str
    created_at: datetime = Field(default_factory=utc_now)
    candidates: list[SearchCandidate]
    attempts: list[SearchAttempt] = Field(min_length=1)


class ErrorEnvelope(StrictModel):
    schema_version: Literal["tracefetch.error.v1"] = "tracefetch.error.v1"
    error: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)


class DoctorCheck(StrictModel):
    name: str
    status: Literal["ok", "warn", "off"]
    detail: str
    capability: str


class DoctorEnvelope(StrictModel):
    schema_version: Literal["tracefetch.doctor.v1"] = "tracefetch.doctor.v1"
    checks: list[DoctorCheck]


class CrawlPage(StrictModel):
    url: str
    depth: int = Field(ge=0)
    status: Literal["complete", "failed", "blocked", "pending"]
    bundle_path: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class CrawlReceipt(StrictModel):
    schema_version: Literal["tracefetch.crawl-receipt.v1"] = "tracefetch.crawl-receipt.v1"
    receipt_kind: Literal["unsigned-self-reported-diagnostic"] = "unsigned-self-reported-diagnostic"
    attested: Literal[False] = False
    trusted_clock: Literal[False] = False
    independent_execution_proven: Literal[False] = False
    crawl_id: str
    root_url: str
    reader: Literal["auto", "direct", "jina", "firecrawl"]
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    crawl_state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime
    status: Literal["running", "complete", "partial", "failed"]
    max_pages: int
    max_depth: int
    pages: list[CrawlPage]
    failures_by_code: dict[str, int] = Field(default_factory=dict)
