from __future__ import annotations

import json
import re
import shutil

# MarkItDown uses fixed argv, no shell, and an executable resolved by shutil.which.
import subprocess  # nosec B404
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, ProcessingInstruction, Tag
from markdownify import markdownify

from tracefetch.adapters.base import ReaderResult
from tracefetch.contracts import LinkRecord
from tracefetch.errors import AdapterUnavailableError, FetchFailedError


@dataclass(slots=True)
class NormalizedDocument:
    markdown: str
    title: str | None
    links: list[LinkRecord]
    warnings: list[str]
    method: str


def normalize_result(result: ReaderResult) -> NormalizedDocument:
    if result.provided_markdown is not None:
        markdown = _clean_markdown(result.provided_markdown)
        return NormalizedDocument(
            markdown=markdown,
            title=_markdown_title(markdown),
            links=_markdown_links(markdown, result.final_url),
            warnings=(
                [] if result.metadata.get("raw_origin_preserved") else ["raw_origin_not_preserved"]
            ),
            method=f"{result.adapter}:provided-markdown",
        )

    media_type = result.content_type.split(";", 1)[0].strip().lower()
    if media_type in {"text/html", "application/xhtml+xml"}:
        return _normalize_html(result.body, result.final_url)
    if media_type in {"application/json", "application/ld+json"}:
        text = _decode(result.body, result.content_type)
        with suppress(json.JSONDecodeError):
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True)
        return NormalizedDocument(
            markdown=f"```json\n{text.strip()}\n```\n",
            title=None,
            links=[],
            warnings=[],
            method="builtin:json",
        )
    if media_type.startswith("text/") or media_type in {
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
    }:
        text = _decode(result.body, result.content_type)
        return NormalizedDocument(
            markdown=_clean_markdown(text),
            title=_markdown_title(text),
            links=_markdown_links(text, result.final_url),
            warnings=[],
            method="builtin:text",
        )
    return _normalize_with_markitdown(result.body, media_type)


def normalize_local_file(path: Path, *, base_url: str | None = None) -> NormalizedDocument:
    suffix = path.suffix.lower()
    body = path.read_bytes()
    if suffix in {".html", ".htm"}:
        return _normalize_html(body, base_url or f"manual://{path.name}")
    if suffix == ".json":
        text = body.decode("utf-8", errors="replace")
        warnings: list[str] = []
        try:
            formatted = json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True)
        except json.JSONDecodeError:
            formatted = text.strip()
            warnings.append("invalid_json_syntax")
        return NormalizedDocument(
            markdown=f"```json\n{formatted}\n```\n",
            title=path.stem,
            links=[],
            warnings=warnings,
            method="builtin:json",
        )
    if suffix in {".md", ".markdown", ".txt", ".xml", ".csv", ".yaml", ".yml"}:
        text = body.decode("utf-8", errors="replace")
        return NormalizedDocument(
            markdown=_clean_markdown(text),
            title=_markdown_title(text) or path.stem,
            links=_markdown_links(text, base_url or f"manual://{path.name}"),
            warnings=[],
            method="builtin:text",
        )
    media_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(suffix, "application/octet-stream")
    return _normalize_with_markitdown(body, media_type, suffix=suffix)


def _normalize_html(body: bytes, base_url: str) -> NormalizedDocument:
    html = body.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    for element in soup(["script", "style", "noscript", "template", "svg"]):
        element.decompose()
    for instruction in soup.find_all(string=lambda node: isinstance(node, ProcessingInstruction)):
        instruction.extract()
    candidate = soup.find("main") or soup.find("article") or soup.body or soup
    if isinstance(candidate, Tag) and len(candidate.get_text(" ", strip=True)) < 160 and soup.body:
        candidate = soup.body
    links: list[LinkRecord] = []
    seen: set[tuple[str, str]] = set()
    for anchor in candidate.find_all("a", href=True):
        href = urljoin(base_url, str(anchor.get("href")))
        text = anchor.get_text(" ", strip=True)
        key = (text, href)
        if text and href.startswith(("http://", "https://")) and key not in seen:
            seen.add(key)
            links.append(LinkRecord(text=text, url=href))
    markdown = markdownify(str(candidate), heading_style="ATX", bullets="-")
    markdown = _clean_markdown(markdown)
    warnings: list[str] = []
    if "�" in markdown:
        warnings.append("decode_replacement_characters_present")
    return NormalizedDocument(
        markdown=markdown,
        title=title or _markdown_title(markdown),
        links=links,
        warnings=warnings,
        method="builtin:html",
    )


def _normalize_with_markitdown(
    body: bytes, media_type: str, *, suffix: str | None = None
) -> NormalizedDocument:
    executable = shutil.which("markitdown")
    if executable is None:
        raise AdapterUnavailableError(
            f"MarkItDown is required for {media_type}; install tracefetch[markitdown]"
        )
    inferred_suffix = suffix or {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    }.get(media_type, ".bin")
    with tempfile.TemporaryDirectory(prefix="tracefetch-") as temp_dir:
        source = Path(temp_dir) / f"source{inferred_suffix}"
        source.write_bytes(body)
        try:
            completed = subprocess.run(  # nosec B603
                [executable, str(source)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchFailedError(f"MarkItDown execution failed: {exc}", retryable=False) from exc
    if completed.returncode != 0:
        raise FetchFailedError(
            completed.stderr.strip() or "MarkItDown conversion failed",
            retryable=False,
            details={"returncode": completed.returncode},
        )
    markdown = _clean_markdown(completed.stdout)
    return NormalizedDocument(
        markdown=markdown,
        title=_markdown_title(markdown),
        links=_markdown_links(markdown, ""),
        warnings=[],
        method="markitdown",
    )


def _decode(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type, flags=re.IGNORECASE)
    charset = match.group(1).strip("\"'") if match else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _clean_markdown(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    return normalized.strip() + "\n" if normalized.strip() else ""


def _markdown_title(markdown: str) -> str | None:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", markdown)
    return match.group(1).strip() if match else None


def _markdown_links(markdown: str, base_url: str) -> list[LinkRecord]:
    links: list[LinkRecord] = []
    seen: set[tuple[str, str]] = set()
    for text, href in re.findall(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)", markdown):
        url = urljoin(base_url, href) if base_url else href
        key = (text.strip(), url)
        if url.startswith(("http://", "https://")) and key not in seen:
            seen.add(key)
            links.append(LinkRecord(text=text.strip(), url=url))
    return links
