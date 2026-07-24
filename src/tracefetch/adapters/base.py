from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from tracefetch.config import Policy
from tracefetch.contracts import SearchCandidate


@dataclass(slots=True)
class ReaderResult:
    adapter: str
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    fetched_at: datetime
    robots_status: str
    provided_markdown: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ReaderAdapter(Protocol):
    name: str
    remote: bool
    authenticated: bool

    def available(self) -> tuple[bool, str]: ...

    def fetch(self, url: str, policy: Policy) -> ReaderResult: ...


class SearchProvider(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...

    def search(self, query: str, limit: int) -> list[SearchCandidate]: ...
