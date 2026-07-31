from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tracefetch.anchors import anchor_text
from tracefetch.contracts import (
    AnchorRecord,
    ArtifactRef,
    CrawlReceipt,
    EvidenceReceipt,
    LinkRecord,
)


def verify_bundle(bundle_dir: Path) -> list[str]:
    failures: list[str] = []
    root = bundle_dir.resolve()
    receipt_path = bundle_dir / "receipt.json"
    if receipt_path.is_symlink():
        return ["unsafe receipt target: receipt.json"]
    try:
        receipt = EvidenceReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        return [f"invalid receipt: {exc}"]

    artifact_data: dict[str, list[tuple[ArtifactRef, bytes]]] = {}
    seen_paths: set[str] = set()
    for artifact in receipt.artifacts:
        relative = Path(artifact.path)
        if relative.is_absolute() or ".." in relative.parts:
            failures.append(f"unsafe artifact path: {artifact.path}")
            continue
        if artifact.path in seen_paths:
            failures.append(f"duplicate artifact path: {artifact.path}")
            continue
        seen_paths.add(artifact.path)
        path = bundle_dir / relative
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            failures.append(f"missing artifact: {artifact.path}")
            continue
        if not resolved.is_relative_to(root) or not resolved.is_file():
            failures.append(f"unsafe artifact target: {artifact.path}")
            continue
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            failures.append(f"unreadable artifact {artifact.path}: {exc}")
            continue
        if len(data) != artifact.bytes:
            failures.append(f"byte count mismatch: {artifact.path}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != artifact.sha256:
            failures.append(f"sha256 mismatch: {artifact.path}")
        artifact_data.setdefault(artifact.role, []).append((artifact, data))

    _verify_layout(receipt, artifact_data, failures)
    _verify_source(receipt, artifact_data, failures)
    _verify_derived(receipt, artifact_data, failures)
    expected_names = {"receipt.json", *seen_paths}
    try:
        observed_names = {item.name for item in bundle_dir.iterdir()}
    except OSError as exc:
        failures.append(f"cannot list bundle directory: {exc}")
    else:
        for name in sorted(observed_names - expected_names):
            failures.append(f"unexpected bundle entry: {name}")
    return failures


def _verify_layout(
    receipt: EvidenceReceipt,
    artifact_data: dict[str, list[tuple[ArtifactRef, bytes]]],
    failures: list[str],
) -> None:
    for role, entries in artifact_data.items():
        if len(entries) > 1:
            failures.append(f"duplicate artifact role: {role}")
    if len(artifact_data.get("raw", [])) != 1:
        failures.append("bundle must contain exactly one raw artifact")
    elif Path(artifact_data["raw"][0][0].path).parent != Path(".") or not Path(
        artifact_data["raw"][0][0].path
    ).name.startswith("raw."):
        failures.append(f"unexpected raw artifact path: {artifact_data['raw'][0][0].path}")
    derived = {role for role in ("normalized", "anchors", "links") if artifact_data.get(role)}
    if derived and derived != {"normalized", "anchors", "links"}:
        failures.append("normalized, anchors, and links artifacts must appear together")
    if receipt.status == "complete" and derived != {"normalized", "anchors", "links"}:
        failures.append("complete bundle must contain all derived artifacts")
    expected_paths = {
        "normalized": "normalized.md",
        "anchors": "anchors.jsonl",
        "links": "links.json",
    }
    for role, expected in expected_paths.items():
        entries = artifact_data.get(role, [])
        if entries and entries[0][0].path != expected:
            failures.append(f"unexpected {role} artifact path: {entries[0][0].path}")
    remote_used = any(name in {"jina", "firecrawl"} for name in receipt.route) or any(
        attempt.status != "skipped" and bool(attempt.metadata.get("remote_adapter"))
        for attempt in receipt.attempts
    )
    authenticated_used = "firecrawl" in receipt.route or any(
        attempt.status != "skipped" and bool(attempt.metadata.get("authenticated_adapter"))
        for attempt in receipt.attempts
    )
    if receipt.policy.remote_adapter_used != remote_used:
        failures.append("remote adapter policy flag does not match route")
    if receipt.policy.authenticated_adapter_used != authenticated_used:
        failures.append("authenticated adapter policy flag does not match route")


def _verify_source(
    receipt: EvidenceReceipt,
    artifact_data: dict[str, list[tuple[ArtifactRef, bytes]]],
    failures: list[str],
) -> None:
    raw_entries = artifact_data.get("raw", [])
    if len(raw_entries) != 1:
        return
    _, raw = raw_entries[0]
    if receipt.source.bytes != len(raw):
        failures.append("source byte count does not match raw artifact")
    if receipt.source.source_sha256 != hashlib.sha256(raw).hexdigest():
        failures.append("source sha256 does not match raw artifact")


def _verify_derived(
    receipt: EvidenceReceipt,
    artifact_data: dict[str, list[tuple[ArtifactRef, bytes]]],
    failures: list[str],
) -> None:
    normalized_entries = artifact_data.get("normalized", [])
    anchor_entries = artifact_data.get("anchors", [])
    link_entries = artifact_data.get("links", [])
    if not (len(normalized_entries) == len(anchor_entries) == len(link_entries) == 1):
        if receipt.quality.normalized_chars != 0:
            failures.append("normalized character count must be zero without derived artifacts")
        return
    try:
        markdown = normalized_entries[0][1].decode("utf-8")
    except UnicodeDecodeError as exc:
        failures.append(f"normalized artifact is not UTF-8: {exc}")
        return
    if len(markdown) != receipt.quality.normalized_chars:
        failures.append("normalized character count mismatch")
    lines = markdown.splitlines()
    anchors = _parse_anchors(anchor_entries[0][1], lines, failures)
    links = _parse_links(link_entries[0][1], failures)
    if len(anchors) != receipt.quality.anchor_count:
        failures.append("anchor count mismatch")
    if len(links) != receipt.quality.link_count:
        failures.append("link count mismatch")


def _parse_anchors(data: bytes, lines: list[str], failures: list[str]) -> list[AnchorRecord]:
    try:
        raw_lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        failures.append(f"anchors artifact is not UTF-8: {exc}")
        return []
    anchors: list[AnchorRecord] = []
    for line_number, raw in enumerate(raw_lines, start=1):
        try:
            anchor = AnchorRecord.model_validate_json(raw)
        except ValidationError as exc:
            failures.append(f"invalid anchor line {line_number}: {exc}")
            continue
        if anchor.line_end > len(lines) or anchor.line_start > anchor.line_end:
            failures.append(f"anchor range invalid: {anchor.anchor_id}")
            continue
        text = anchor_text(lines, anchor.line_start, anchor.line_end)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != anchor.sha256:
            failures.append(f"anchor sha256 mismatch: {anchor.anchor_id}")
            continue
        anchors.append(anchor)
    return anchors


def _parse_links(data: bytes, failures: list[str]) -> list[LinkRecord]:
    try:
        raw: Any = json.loads(data)
        if not isinstance(raw, list):
            raise ValueError("links root must be a list")
        return [LinkRecord.model_validate(item) for item in raw]
    except (UnicodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        failures.append(f"invalid links artifact: {exc}")
        return []


def verification_payload(bundle_dir: Path) -> dict[str, object]:
    failures = verify_bundle(bundle_dir)
    return {
        "schema_version": "tracefetch.verification.v1",
        "bundle": bundle_dir.name,
        "valid": not failures,
        "failures": failures,
    }


def _load_crawl_receipt(receipt_path: Path) -> tuple[CrawlReceipt | None, list[str]]:
    if receipt_path.is_symlink():
        return None, ["crawl receipt is unreadable"]
    try:
        source = receipt_path.read_text(encoding="utf-8")
    except UnicodeError:
        return None, ["crawl receipt is not valid UTF-8"]
    except OSError:
        return None, ["crawl receipt is unreadable"]
    try:
        return CrawlReceipt.model_validate_json(source), []
    except ValidationError:
        return None, ["crawl receipt failed schema validation"]


def _crawl_verification_result(root_dir: Path) -> tuple[CrawlReceipt | None, list[str]]:
    failures: list[str] = []
    root = root_dir.resolve()
    receipt_path = root_dir / "crawl-receipt.json"
    state_path = root_dir / "crawl.sqlite3"
    receipt, receipt_failures = _load_crawl_receipt(receipt_path)
    if receipt is None:
        return None, receipt_failures
    if state_path.is_symlink():
        return receipt, ["crawl receipt and state must not be symlinks"]
    try:
        state_digest = _sha256_file(state_path)
    except OSError as exc:
        failures.append(f"invalid crawl state: {exc}")
    else:
        if state_digest != receipt.crawl_state_sha256:
            failures.append("crawl state sha256 mismatch")

    urls: set[str] = set()
    failures_by_code = Counter[str]()
    for page in receipt.pages:
        if page.url in urls:
            failures.append(f"duplicate crawl page URL: {page.url}")
        urls.add(page.url)
        if page.depth > receipt.max_depth:
            failures.append(f"crawl page exceeds max_depth: {page.url}")
        if page.error_code:
            failures_by_code[page.error_code] += 1
        if page.status == "complete":
            if not page.bundle_path:
                failures.append(f"complete crawl page lacks bundle_path: {page.url}")
                continue
            bundle = _safe_child(root, page.bundle_path, failures)
            if bundle is None or not bundle.is_dir():
                failures.append(f"invalid crawl page bundle: {page.url}")
                continue
            page_failures = verify_bundle(bundle)
            failures.extend(f"{page.url}: {failure}" for failure in page_failures)
            try:
                evidence = EvidenceReceipt.model_validate_json(
                    (bundle / "receipt.json").read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, ValidationError):
                continue
            if evidence.source.requested_url != page.url:
                failures.append(f"crawl page URL does not match bundle source: {page.url}")
        elif page.bundle_path is not None:
            failures.append(f"non-complete crawl page has bundle_path: {page.url}")
    if dict(sorted(failures_by_code.items())) != dict(sorted(receipt.failures_by_code.items())):
        failures.append("failures_by_code does not match page records")
    statuses = Counter(page.status for page in receipt.pages)
    processed_count = statuses["complete"] + statuses["failed"] + statuses["blocked"]
    if statuses["pending"] and processed_count >= receipt.max_pages:
        expected_status = "partial"
    elif statuses["pending"]:
        expected_status = "running"
    elif statuses["complete"] and (statuses["failed"] or statuses["blocked"]):
        expected_status = "partial"
    elif statuses["complete"]:
        expected_status = "complete"
    else:
        expected_status = "failed"
    if receipt.status != expected_status:
        failures.append("crawl status does not match page records")
    failures.extend(_verify_crawl_state(receipt, state_path))
    return receipt, failures


def verify_crawl_bundle(root_dir: Path) -> list[str]:
    return _crawl_verification_result(root_dir)[1]


def crawl_verification_payload(root_dir: Path) -> dict[str, object]:
    receipt, failures = _crawl_verification_result(root_dir)
    verified_pages = (
        0 if receipt is None else sum(page.status == "complete" for page in receipt.pages)
    )
    return {
        "schema_version": "tracefetch.crawl-verification.v1",
        "crawl_id": "" if receipt is None else receipt.crawl_id,
        "valid": not failures,
        "verified_pages": verified_pages,
        "failures": failures,
    }


def _verify_crawl_state(receipt: CrawlReceipt, state_path: Path) -> list[str]:
    failures: list[str] = []
    connection: sqlite3.Connection | None = None
    metadata: dict[str, str] = {}
    state_pages: list[dict[str, object]] = []
    invalid_state = False
    try:
        connection = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("select key, value from metadata")
        }
        rows = list(
            connection.execute(
                "select url, depth, status, bundle_path, error_code, error_message "
                "from pages order by depth, added_at"
            )
        )
        state_pages = [
            {
                "url": str(row["url"]),
                "depth": int(row["depth"]),
                "status": "pending" if row["status"] == "running" else str(row["status"]),
                "bundle_path": row["bundle_path"],
                "error_code": row["error_code"],
                "error_message": row["error_message"],
            }
            for row in rows
        ]
    except (sqlite3.Error, TypeError, ValueError, OverflowError):
        invalid_state = True
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                invalid_state = True
    if invalid_state:
        return ["crawl SQLite state is invalid"]
    expected_metadata = {
        "crawl_id": receipt.crawl_id,
        "root_url": receipt.root_url,
        "reader": receipt.reader,
        "policy_sha256": receipt.policy_sha256,
        "max_pages": str(receipt.max_pages),
        "max_depth": str(receipt.max_depth),
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            failures.append(f"crawl state metadata mismatch: {key}")
    receipt_pages = [
        page.model_dump(
            mode="json",
            include={"url", "depth", "status", "bundle_path", "error_code", "error_message"},
        )
        for page in receipt.pages
    ]
    if state_pages != receipt_pages:
        failures.append("crawl state page projection mismatch")
    return failures


def _safe_child(root: Path, value: str, failures: list[str]) -> Path | None:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        failures.append(f"unsafe crawl bundle path: {value}")
        return None
    try:
        resolved = (root / relative).resolve(strict=True)
    except OSError:
        failures.append(f"missing crawl bundle path: {value}")
        return None
    if not resolved.is_relative_to(root):
        failures.append(f"crawl bundle path escapes root: {value}")
        return None
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
