from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Literal

from tracefetch.adapters.base import ReaderResult
from tracefetch.anchors import build_anchors
from tracefetch.config import Policy
from tracefetch.contracts import (
    ArtifactRef,
    Attempt,
    EvidenceReceipt,
    PolicyRecord,
    QualityRecord,
    SourceRecord,
)
from tracefetch.errors import InvalidInputError
from tracefetch.normalize import NormalizedDocument


@dataclass(frozen=True, slots=True)
class BundleResult:
    path: Path
    receipt: EvidenceReceipt


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bundle(
    output_dir: Path,
    raw_result: ReaderResult,
    normalized: NormalizedDocument | None,
    attempts: list[Attempt],
    policy: Policy,
    route: list[str],
    warnings: list[str] | None = None,
) -> BundleResult:
    if output_dir.exists():
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise InvalidInputError(f"output path is not an ordinary directory: {output_dir}")
        if any(output_dir.iterdir()):
            raise InvalidInputError(f"output directory is not empty: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name or 'bundle'}.", dir=output_dir.parent)
    )
    try:
        staged = _write_bundle_files(
            staging_dir,
            raw_result,
            normalized,
            attempts,
            policy,
            route,
            warnings,
        )
        if output_dir.exists():
            output_dir.rmdir()
        os.replace(staging_dir, output_dir)
        return BundleResult(path=output_dir, receipt=staged.receipt)
    except Exception:
        with suppress(FileNotFoundError):
            shutil.rmtree(staging_dir)
        raise


def _write_bundle_files(
    output_dir: Path,
    raw_result: ReaderResult,
    normalized: NormalizedDocument | None,
    attempts: list[Attempt],
    policy: Policy,
    route: list[str],
    warnings: list[str] | None,
) -> BundleResult:
    warnings = list(warnings or [])
    artifacts: list[ArtifactRef] = []

    raw_name, raw_media_type = _raw_name(raw_result.content_type)
    _atomic_write(output_dir / raw_name, raw_result.body)
    artifacts.append(_artifact(output_dir, raw_name, "raw", raw_media_type))

    anchors = []
    links = []
    normalized_chars = 0
    if normalized is not None and normalized.markdown:
        markdown_bytes = normalized.markdown.encode("utf-8")
        _atomic_write(output_dir / "normalized.md", markdown_bytes)
        artifacts.append(_artifact(output_dir, "normalized.md", "normalized", "text/markdown"))
        anchors = build_anchors(normalized.markdown)
        anchor_payload = "".join(
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
            for item in anchors
        ).encode("utf-8")
        _atomic_write(output_dir / "anchors.jsonl", anchor_payload)
        artifacts.append(_artifact(output_dir, "anchors.jsonl", "anchors", "application/x-ndjson"))
        links = normalized.links
        links_payload = (
            json.dumps(
                [item.model_dump(mode="json") for item in links],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        _atomic_write(output_dir / "links.json", links_payload)
        artifacts.append(_artifact(output_dir, "links.json", "links", "application/json"))
        normalized_chars = len(normalized.markdown)
        warnings.extend(normalized.warnings)

    source_hash = sha256_bytes(raw_result.body)
    timestamp = raw_result.fetched_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    receipt_id = f"tf_{source_hash[:16]}_{timestamp}"
    remote_used = any(name in {"jina", "firecrawl"} for name in route) or any(
        attempt.status != "skipped" and bool(attempt.metadata.get("remote_adapter"))
        for attempt in attempts
    )
    authenticated_used = "firecrawl" in route or any(
        attempt.status != "skipped" and bool(attempt.metadata.get("authenticated_adapter"))
        for attempt in attempts
    )
    thin = 0 < normalized_chars < policy.min_normalized_chars
    status: Literal["complete", "partial", "failed"] = (
        "complete" if normalized_chars >= policy.min_normalized_chars else "partial"
    )
    if normalized is None:
        warnings.append("normalization_unavailable")
    if thin:
        warnings.append("normalized_content_below_threshold")
    warnings.append("source_content_is_untrusted_data")
    policy_warnings = [
        "robots.txt is a crawl preference signal, not access authorization",
        "terms and source license require task-specific review",
    ]
    if remote_used:
        policy_warnings.append("public source content was sent to a third-party reader")

    receipt = EvidenceReceipt(
        receipt_id=receipt_id,
        status=status,
        source=SourceRecord(
            requested_url=raw_result.requested_url,
            normalized_url=raw_result.requested_url,
            final_url=raw_result.final_url,
            fetched_at=raw_result.fetched_at,
            status_code=raw_result.status_code,
            content_type=raw_result.content_type,
            bytes=len(raw_result.body),
            title=normalized.title if normalized else None,
            source_sha256=source_hash,
        ),
        route=route,
        attempts=attempts,
        artifacts=artifacts,
        policy=PolicyRecord(
            robots_status=raw_result.robots_status,  # type: ignore[arg-type]
            private_networks_denied=policy.deny_private_networks,
            remote_adapter_used=remote_used,
            authenticated_adapter_used=authenticated_used,
            warnings=policy_warnings,
        ),
        quality=QualityRecord(
            normalized_chars=normalized_chars,
            anchor_count=len(anchors),
            link_count=len(links),
            thin_content=thin,
        ),
        warnings=sorted(set(warnings)),
    )
    receipt_payload = (
        json.dumps(
            receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        + b"\n"
    )
    _atomic_write(output_dir / "receipt.json", receipt_payload)
    return BundleResult(path=output_dir, receipt=receipt)


def _artifact(root: Path, name: str, role: str, media_type: str) -> ArtifactRef:
    data = (root / name).read_bytes()
    return ArtifactRef(
        role=role,  # type: ignore[arg-type]
        path=name,
        media_type=media_type,
        sha256=sha256_bytes(data),
        bytes=len(data),
    )


def _raw_name(content_type: str) -> tuple[str, str]:
    media_type = content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
    extension = {
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "application/json": ".json",
        "application/ld+json": ".json",
        "application/pdf": ".pdf",
        "text/markdown": ".md",
        "text/plain": ".txt",
        "application/xml": ".xml",
        "application/rss+xml": ".xml",
        "application/atom+xml": ".xml",
    }.get(media_type, ".bin")
    return f"raw{extension}", media_type


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
