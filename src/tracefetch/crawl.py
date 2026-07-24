from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

from tracefetch.config import Policy
from tracefetch.contracts import CrawlPage, CrawlReceipt
from tracefetch.errors import InvalidInputError, TraceFetchError
from tracefetch.pipeline import fetch_to_bundle
from tracefetch.security import normalize_url, validate_public_url
from tracefetch.verify import verify_bundle


def utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class CrawlStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            create table if not exists metadata (
              key text primary key,
              value text not null
            );
            create table if not exists pages (
              url text primary key,
              depth integer not null,
              status text not null,
              discovered_from text,
              attempts integer not null default 0,
              bundle_path text,
              error_code text,
              error_message text,
              retryable integer not null default 0,
              added_at text not null,
              updated_at text not null
            );
            create index if not exists pages_queue_idx on pages(status, depth, added_at);
            """
        )

    def close(self) -> None:
        self.connection.close()

    def has_job(self) -> bool:
        return self.get_meta("crawl_id") is not None

    def create_job(self, root_url: str, reader: str, policy: Policy) -> str:
        created_at = utc_iso()
        crawl_id = "crawl_" + hashlib.sha256(f"{root_url}|{created_at}".encode()).hexdigest()[:16]
        for key, value in {
            "crawl_id": crawl_id,
            "root_url": root_url,
            "reader": reader,
            "policy_sha256": policy_sha256(policy),
            "created_at": created_at,
            "updated_at": created_at,
            "max_pages": str(policy.max_pages),
            "max_depth": str(policy.max_depth),
        }.items():
            self.connection.execute(
                "insert or replace into metadata(key, value) values (?, ?)", (key, value)
            )
        self.enqueue(root_url, 0, None)
        self.connection.commit()
        return crawl_id

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute("select value from metadata where key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "insert or replace into metadata(key, value) values (?, ?)", (key, value)
        )
        self.connection.commit()

    def enqueue(self, url: str, depth: int, discovered_from: str | None) -> None:
        now = utc_iso()
        self.connection.execute(
            """
            insert or ignore into pages
              (url, depth, status, discovered_from, added_at, updated_at)
            values (?, ?, 'pending', ?, ?, ?)
            """,
            (url, depth, discovered_from, now, now),
        )

    def next_pending(self) -> sqlite3.Row | None:
        row = self.connection.execute(
            "select * from pages where status = 'pending' order by depth, added_at limit 1"
        ).fetchone()
        return row if isinstance(row, sqlite3.Row) else None

    def mark_running(self, url: str) -> None:
        self.connection.execute(
            """
            update pages
            set status = 'running', attempts = attempts + 1, updated_at = ?
            where url = ?
            """,
            (utc_iso(), url),
        )
        self.connection.commit()

    def mark_complete(self, url: str, bundle_path: str) -> None:
        self.connection.execute(
            """
            update pages set status = 'complete', bundle_path = ?, error_code = null,
              error_message = null, retryable = 0, updated_at = ? where url = ?
            """,
            (bundle_path, utc_iso(), url),
        )
        self.connection.commit()

    def mark_error(self, url: str, error: TraceFetchError, *, retry: bool) -> None:
        status = "pending" if retry else ("blocked" if error.code == "policy_blocked" else "failed")
        self.connection.execute(
            """
            update pages set status = ?, error_code = ?, error_message = ?,
              retryable = ?, updated_at = ? where url = ?
            """,
            (status, error.code, error.message, int(error.retryable), utc_iso(), url),
        )
        self.connection.commit()

    def processed_count(self) -> int:
        row = self.connection.execute(
            "select count(*) as count from pages where status in ('complete', 'failed', 'blocked')"
        ).fetchone()
        return int(row["count"])

    def rows(self) -> list[sqlite3.Row]:
        return list(self.connection.execute("select * from pages order by depth, added_at"))

    def recover_running(self) -> None:
        self.connection.execute(
            "update pages set status = 'pending', updated_at = ? where status = 'running'",
            (utc_iso(),),
        )
        self.connection.commit()


def crawl_site(
    root_url: str,
    output_dir: Path,
    *,
    reader: str,
    policy: Policy,
    resume: bool,
) -> CrawlReceipt:
    root = validate_public_url(root_url, policy)
    if output_dir.exists():
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise InvalidInputError(f"crawl output is not an ordinary directory: {output_dir}")
        if not resume and any(output_dir.iterdir()):
            raise InvalidInputError(f"crawl output directory is not empty: {output_dir}")
        if resume and not (output_dir / "crawl.sqlite3").is_file():
            raise InvalidInputError("--resume requested but crawl.sqlite3 is missing")
    elif resume:
        raise InvalidInputError("--resume requested but crawl output does not exist")
    output_dir.mkdir(parents=True, exist_ok=True)
    store = CrawlStore(output_dir / "crawl.sqlite3")
    try:
        if store.has_job():
            if not resume:
                raise InvalidInputError("crawl state already exists; pass --resume to continue")
            if store.get_meta("root_url") != root:
                raise InvalidInputError("resume root URL does not match existing crawl")
            if store.get_meta("reader") != reader:
                raise InvalidInputError("resume reader does not match existing crawl")
            if store.get_meta("policy_sha256") != policy_sha256(policy):
                raise InvalidInputError("resume policy does not match existing crawl")
            store.recover_running()
        else:
            if resume:
                raise InvalidInputError("--resume requested but no crawl state exists")
            store.create_job(root, reader, policy)

        last_request: dict[str, float] = {}
        origin = _origin(root)
        while store.processed_count() < policy.max_pages:
            row = store.next_pending()
            if row is None:
                break
            url = str(row["url"])
            depth = int(row["depth"])
            page_name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
            page_dir = output_dir / "pages" / page_name
            if (page_dir / "receipt.json").is_file() and not verify_bundle(page_dir):
                store.mark_complete(url, str(page_dir.relative_to(output_dir)))
                _enqueue_bundle_links(store, page_dir, url, depth, policy.max_depth, origin)
                store.set_meta("updated_at", utc_iso())
                _write_receipt(output_dir, _receipt_from_store(store))
                continue
            host = urlsplit(url).hostname or ""
            elapsed = time.monotonic() - last_request.get(host, 0.0)
            if elapsed < policy.min_interval_seconds:
                time.sleep(policy.min_interval_seconds - elapsed)
            store.mark_running(url)
            last_request[host] = time.monotonic()
            try:
                result = fetch_to_bundle(url, page_dir, reader=reader, policy=policy)
                relative_bundle = str(result.path.relative_to(output_dir))
                store.mark_complete(url, relative_bundle)
                _enqueue_bundle_links(store, result.path, url, depth, policy.max_depth, origin)
            except TraceFetchError as exc:
                attempts = int(row["attempts"]) + 1
                retry = exc.retryable and attempts < 2
                store.mark_error(url, exc, retry=retry)
            store.set_meta("updated_at", utc_iso())
            _write_receipt(output_dir, _receipt_from_store(store))
        receipt = _receipt_from_store(store)
        _write_receipt(output_dir, receipt)
        return receipt
    finally:
        store.close()


def load_crawl_receipt(path: Path) -> CrawlReceipt:
    return CrawlReceipt.model_validate_json(path.read_text(encoding="utf-8"))


def _receipt_from_store(store: CrawlStore) -> CrawlReceipt:
    rows = store.rows()
    pages = [
        CrawlPage(
            url=str(row["url"]),
            depth=int(row["depth"]),
            status=("pending" if row["status"] == "running" else row["status"]),
            bundle_path=row["bundle_path"],
            error_code=row["error_code"],
            error_message=row["error_message"],
        )
        for row in rows
    ]
    statuses = Counter(page.status for page in pages)
    max_pages = int(store.get_meta("max_pages") or 0)
    status: Literal["running", "complete", "partial", "failed"]
    if statuses["pending"] and store.processed_count() >= max_pages:
        status = "partial"
    elif statuses["pending"]:
        status = "running"
    elif statuses["complete"] and (statuses["failed"] or statuses["blocked"]):
        status = "partial"
    elif statuses["complete"]:
        status = "complete"
    else:
        status = "failed"
    failures = Counter(page.error_code for page in pages if page.error_code)
    return CrawlReceipt(
        crawl_id=store.get_meta("crawl_id") or "crawl_unknown",
        root_url=store.get_meta("root_url") or "",
        reader=cast(
            Literal["auto", "direct", "jina", "firecrawl"],
            store.get_meta("reader") or "auto",
        ),
        policy_sha256=store.get_meta("policy_sha256") or "0" * 64,
        crawl_state_sha256=_sha256_file(store.path),
        created_at=datetime.fromisoformat(store.get_meta("created_at") or utc_iso()),
        updated_at=datetime.fromisoformat(store.get_meta("updated_at") or utc_iso()),
        status=status,
        max_pages=max_pages,
        max_depth=int(store.get_meta("max_depth") or 0),
        pages=pages,
        failures_by_code={str(key): value for key, value in failures.items()},
    )


def _bundle_links(bundle_dir: Path) -> list[str]:
    path = bundle_dir / "links.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [str(item.get("url")) for item in payload if isinstance(item, dict) and item.get("url")]


def _enqueue_bundle_links(
    store: CrawlStore,
    bundle_dir: Path,
    source_url: str,
    depth: int,
    max_depth: int,
    origin: tuple[str, str, int | None],
) -> None:
    if depth >= max_depth:
        return
    for link in _bundle_links(bundle_dir):
        try:
            normalized = normalize_url(link)
        except TraceFetchError:
            continue
        if _origin(normalized) != origin:
            continue
        store.enqueue(normalized, depth + 1, source_url)
    store.connection.commit()


def _write_receipt(output_dir: Path, receipt: CrawlReceipt) -> None:
    payload = json.dumps(
        receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    temporary = output_dir / ".crawl-receipt.json.tmp"
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(output_dir / "crawl-receipt.json")


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname or "", parsed.port or default_port


def policy_sha256(policy: Policy) -> str:
    payload = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
