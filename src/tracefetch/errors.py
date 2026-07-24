from __future__ import annotations

from typing import Any


class TraceFetchError(Exception):
    """Base error with stable agent-facing metadata."""

    code = "tracefetch_error"
    exit_code = 5
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = self.retryable if retryable is None else retryable
        self.details = details or {}


class InvalidInputError(TraceFetchError):
    code = "invalid_input"
    exit_code = 2


class PolicyBlockedError(TraceFetchError):
    code = "policy_blocked"
    exit_code = 3


class AdapterUnavailableError(TraceFetchError):
    code = "adapter_unavailable"
    exit_code = 4


class FetchFailedError(TraceFetchError):
    code = "fetch_failed"
    exit_code = 5
    retryable = True


class RateLimitedError(FetchFailedError):
    code = "rate_limited"


class SearchFailedError(TraceFetchError):
    code = "search_failed"
    exit_code = 5
    retryable = True


class ProviderProtocolError(SearchFailedError):
    code = "provider_protocol_error"
    retryable = False


class VerificationError(TraceFetchError):
    code = "verification_failed"
    exit_code = 6
