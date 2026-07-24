from __future__ import annotations

import mimetypes
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from tracefetch.adapters.base import ReaderAdapter, ReaderResult
from tracefetch.adapters.readers import DirectReader, FirecrawlReader, JinaReader
from tracefetch.bundle import BundleResult, write_bundle
from tracefetch.config import Policy
from tracefetch.contracts import Attempt
from tracefetch.errors import AdapterUnavailableError, InvalidInputError, TraceFetchError
from tracefetch.normalize import NormalizedDocument, normalize_local_file, normalize_result
from tracefetch.robots import RobotsPolicy


def fetch_to_bundle(
    url: str,
    output_dir: Path,
    *,
    reader: str,
    policy: Policy,
    adapters: Mapping[str, ReaderAdapter] | None = None,
) -> BundleResult:
    robots = RobotsPolicy()
    registry: dict[str, ReaderAdapter] = {
        "direct": DirectReader(robots),
        "jina": JinaReader(robots),
        "firecrawl": FirecrawlReader(robots),
    }
    for name, adapter in (adapters or {}).items():
        if name in registry:
            raise InvalidInputError(f"reader adapter name is reserved: {name}")
        if adapter.name != name:
            raise InvalidInputError(f"reader adapter key {name!r} does not match {adapter.name!r}")
        registry[name] = adapter
    names = _reader_route(reader, policy, set(registry))
    attempts: list[Attempt] = []
    route: list[str] = []
    raw_result: ReaderResult | None = None
    normalized: NormalizedDocument | None = None
    warnings: list[str] = []
    last_error: TraceFetchError | None = None

    for name in names:
        adapter = registry[name]
        adapter_metadata = {
            "remote_adapter": adapter.remote,
            "authenticated_adapter": adapter.authenticated,
        }
        available, reason = adapter.available()
        started = datetime.now(UTC)
        if not available:
            attempts.append(
                Attempt(
                    adapter=name,
                    status="skipped",
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    code="adapter_unavailable",
                    message=reason,
                    metadata=adapter_metadata,
                )
            )
            continue
        route.append(name)
        try:
            result = adapter.fetch(url, policy)
            if raw_result is None:
                raw_result = result
            try:
                candidate = normalize_result(result)
            except TraceFetchError as exc:
                last_error = exc
                attempts.append(
                    Attempt(
                        adapter=name,
                        status="success",
                        started_at=started,
                        completed_at=datetime.now(UTC),
                        http_status=result.status_code,
                        metadata={
                            **adapter_metadata,
                            "raw_bytes_acquired": len(result.body),
                        },
                    )
                )
                attempts.append(
                    Attempt(
                        adapter=f"{name}:normalize",
                        status="failed",
                        started_at=started,
                        completed_at=datetime.now(UTC),
                        code=exc.code,
                        message=exc.message,
                        retryable=exc.retryable,
                        metadata=adapter_metadata,
                    )
                )
                continue
            normalized = candidate
            attempts.append(
                Attempt(
                    adapter=name,
                    status="success",
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    http_status=result.status_code,
                    metadata={
                        **adapter_metadata,
                        "normalization_method": candidate.method,
                    },
                )
            )
            if len(candidate.markdown) >= policy.min_normalized_chars or reader != "auto":
                break
            warnings.append(f"{name}_returned_thin_content")
        except TraceFetchError as exc:
            last_error = exc
            attempts.append(
                Attempt(
                    adapter=name,
                    status="failed",
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    code=exc.code,
                    message=exc.message,
                    retryable=exc.retryable,
                    http_status=_http_status(exc),
                    metadata={**adapter_metadata, **exc.details},
                )
            )
            if reader != "auto":
                raise

    if raw_result is None:
        if last_error is not None:
            raise last_error
        raise AdapterUnavailableError("no reader adapter is available")
    return write_bundle(output_dir, raw_result, normalized, attempts, policy, route, warnings)


def ingest_to_bundle(
    path: Path,
    output_dir: Path,
    *,
    source_url: str | None,
    policy: Policy,
) -> BundleResult:
    if not path.is_file():
        raise InvalidInputError(f"input file does not exist: {path}")
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise InvalidInputError(f"unable to read input file {path}: {exc}") from exc
    if len(body) > policy.max_bytes:
        raise InvalidInputError(f"input file exceeds max_bytes: {policy.max_bytes}")
    logical_source = source_url or f"manual://{path.name}"
    started = datetime.now(UTC)
    normalized: NormalizedDocument | None
    attempts: list[Attempt]
    try:
        normalized = normalize_local_file(path, base_url=logical_source)
        attempts = [
            Attempt(
                adapter="local-file",
                status="success",
                started_at=started,
                completed_at=datetime.now(UTC),
                metadata={"normalization_method": normalized.method},
            )
        ]
    except TraceFetchError as exc:
        normalized = None
        attempts = [
            Attempt(
                adapter="local-file:normalize",
                status="failed",
                started_at=started,
                completed_at=datetime.now(UTC),
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
            )
        ]
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    result = ReaderResult(
        adapter="local-file",
        requested_url=logical_source,
        final_url=logical_source,
        status_code=200,
        content_type=media_type,
        body=body,
        fetched_at=datetime.now(UTC),
        robots_status="unchecked",
        metadata={"input_name": path.name},
    )
    return write_bundle(
        output_dir,
        result,
        normalized,
        attempts,
        policy,
        ["local-file"],
        ["manual_ingest_does_not_establish_source_authorization"],
    )


def _reader_route(
    reader: str,
    policy: Policy,
    reader_names: set[str] | None = None,
) -> list[str]:
    known = reader_names or {"direct", "jina", "firecrawl"}
    if reader in known:
        return [reader]
    if reader != "auto":
        raise AdapterUnavailableError(f"unknown reader: {reader}")
    route = ["direct"]
    if policy.allow_remote_adapters:
        route.append("jina")
        if policy.allow_authenticated_adapters:
            route.append("firecrawl")
    return route


def _http_status(error: TraceFetchError) -> int | None:
    value = error.details.get("http_status")
    return int(value) if isinstance(value, int | str) and str(value).isdigit() else None
