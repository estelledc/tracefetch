from __future__ import annotations

import json
import re
import shutil

# Subprocesses use fixed argv, no shell, and executables resolved by shutil.which.
import subprocess  # nosec B404
from typing import Any

from tracefetch.contracts import SearchCandidate
from tracefetch.errors import AdapterUnavailableError, FetchFailedError, RateLimitedError

EXA_SNIPPET_LIMIT = 1_200
EXA_TRUNCATION_MARKER = "\n[truncated]"
PROVIDER_ERROR_LIMIT = 320
GITHUB_QUERY_STOPWORDS = {
    "a",
    "ai",
    "an",
    "best",
    "build",
    "code",
    "device",
    "example",
    "examples",
    "for",
    "github",
    "how",
    "implementation",
    "in",
    "of",
    "protocol",
    "source",
    "the",
    "to",
    "with",
}


class ExaSearchProvider:
    name = "exa"

    def available(self) -> tuple[bool, str]:
        present = shutil.which("mcporter") is not None
        return present, "mcporter available" if present else "mcporter missing"

    def search(self, query: str, limit: int) -> list[SearchCandidate]:
        executable = shutil.which("mcporter")
        if executable is None:
            raise AdapterUnavailableError("mcporter is not installed")
        expression = f"exa.web_search_exa(query: {json.dumps(query)}, numResults: {limit})"
        try:
            completed = subprocess.run(  # nosec B603
                [executable, "call", expression],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchFailedError(f"Exa search execution failed: {exc}", retryable=True) from exc
        if completed.returncode != 0:
            message, rate_limited = _compact_provider_error(
                completed.stderr,
                fallback="Exa search failed",
            )
            error_type = RateLimitedError if rate_limited else FetchFailedError
            raise error_type(
                message,
                retryable=True,
                details={"returncode": completed.returncode},
            )
        return _parse_exa_output(completed.stdout, limit)


class GitHubSearchProvider:
    name = "github"

    def available(self) -> tuple[bool, str]:
        present = shutil.which("gh") is not None
        return present, "gh available" if present else "gh missing"

    def search(self, query: str, limit: int) -> list[SearchCandidate]:
        executable = shutil.which("gh")
        if executable is None:
            raise AdapterUnavailableError("gh is not installed")
        for index, variant in enumerate(_github_query_variants(query)):
            payload = _run_github_search(
                executable,
                variant,
                limit,
                sort_by_stars=index > 0,
            )
            if payload:
                return _github_candidates(
                    payload,
                    query_variant=variant,
                    relaxed=index > 0,
                )
        return []


def _run_github_search(
    executable: str,
    query: str,
    limit: int,
    *,
    sort_by_stars: bool,
) -> list[dict[str, Any]]:
    arguments = [
        executable,
        "search",
        "repos",
        query,
        "--limit",
        str(limit),
        "--json",
        "fullName,description,stargazersCount,url,updatedAt,license",
    ]
    if sort_by_stars:
        arguments.extend(["--sort", "stars"])
    try:
        completed = subprocess.run(  # nosec B603
            arguments,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FetchFailedError(f"GitHub search execution failed: {exc}", retryable=True) from exc
    if completed.returncode != 0:
        message, rate_limited = _compact_provider_error(
            completed.stderr,
            fallback="GitHub search failed",
        )
        error_type = RateLimitedError if rate_limited else FetchFailedError
        raise error_type(
            message,
            retryable=True,
            details={"returncode": completed.returncode, "query_variant": query},
        )
    try:
        decoded: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FetchFailedError("GitHub search returned invalid JSON", retryable=False) from exc
    if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
        raise FetchFailedError("GitHub search returned an unexpected shape", retryable=False)
    return decoded


def _github_candidates(
    payload: list[dict[str, Any]],
    *,
    query_variant: str,
    relaxed: bool,
) -> list[SearchCandidate]:
    return [
        SearchCandidate(
            rank=index,
            title=str(item.get("fullName") or "Untitled repository"),
            url=str(item.get("url") or ""),
            snippet=str(item.get("description") or ""),
            provider="github",
            published_at=item.get("updatedAt"),
            metadata={
                "stars": item.get("stargazersCount"),
                "license": item.get("license"),
                "query_variant": query_variant,
                "query_relaxed": relaxed,
            },
        )
        for index, item in enumerate(payload, start=1)
        if item.get("url")
    ]


def _github_query_variants(query: str) -> list[str]:
    normalized = " ".join(query.split())
    variants = [normalized]
    tokens = re.findall(r'"[^"]+"|\S+', normalized)
    qualifiers = [token for token in tokens if ":" in token or token.startswith("-")]
    topics = [
        token
        for token in tokens
        if token not in qualifiers and token.strip('"').lower() not in GITHUB_QUERY_STOPWORDS
    ]
    if len(topics) >= 2:
        relaxed = " ".join([*topics[:2], *qualifiers])
        if relaxed and relaxed != normalized:
            variants.append(relaxed)
    return variants


def _parse_exa_output(raw: str, limit: int) -> list[SearchCandidate]:
    sections = re.split(r"(?m)^Title:\s*", raw)
    candidates: list[SearchCandidate] = []
    for section in sections[1:]:
        title, _, rest = section.partition("\n")
        url_match = re.search(r"(?m)^URL:\s*(\S+)", rest)
        if not url_match:
            continue
        published_match = re.search(r"(?m)^Published:\s*(.+)$", rest)
        highlights_match = re.search(r"(?ms)^Highlights:\s*(.*?)(?=\n---\s*$|\Z)", rest)
        snippet = _compact_exa_snippet(highlights_match.group(1) if highlights_match else "")
        candidates.append(
            SearchCandidate(
                rank=len(candidates) + 1,
                title=title.strip() or "Untitled result",
                url=url_match.group(1).strip(),
                snippet=snippet,
                provider="exa",
                metadata={"snippet_truncated": snippet.endswith(EXA_TRUNCATION_MARKER)},
                published_at=(
                    published_match.group(1).strip()
                    if published_match and published_match.group(1).strip() not in {"N/A", "null"}
                    else None
                ),
            )
        )
        if len(candidates) >= limit:
            break
    if not candidates and raw.strip():
        for index, url in enumerate(re.findall(r"https?://[^\s)]+", raw), start=1):
            candidates.append(
                SearchCandidate(
                    rank=index,
                    title=url,
                    url=url.rstrip(".,"),
                    snippet="",
                    provider="exa",
                )
            )
            if len(candidates) >= limit:
                break
    return candidates


def _compact_exa_snippet(raw: str) -> str:
    lines: list[str] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line == "..." or (lines and lines[-1] == line):
            continue
        lines.append(line)
    compact = "\n".join(lines)
    if len(compact) <= EXA_SNIPPET_LIMIT:
        return compact
    body_limit = EXA_SNIPPET_LIMIT - len(EXA_TRUNCATION_MARKER)
    return compact[:body_limit].rstrip() + EXA_TRUNCATION_MARKER


def _compact_provider_error(raw: str, *, fallback: str) -> tuple[str, bool]:
    plain = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    rate_limited = bool(re.search(r"(?:http\s*)?429|rate[ -]?limit", plain, re.IGNORECASE))
    if rate_limited:
        return "upstream search rate limit reached (HTTP 429)", True

    lines: list[str] = []
    for raw_line in plain.splitlines():
        line = " ".join(raw_line.split())
        if not line or line.startswith("at ") or line.startswith("StreamableHTTPError:"):
            continue
        line = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", line)
        line = re.sub(r"(?i)(exaApiKey=)[^\s&\"']+", r"\1[redacted]", line)
        lines.append(line)
        if len(" ".join(lines)) >= PROVIDER_ERROR_LIMIT:
            break
    message = " ".join(lines) or fallback
    if len(message) > PROVIDER_ERROR_LIMIT:
        message = message[: PROVIDER_ERROR_LIMIT - len(" [truncated]")].rstrip()
        message += " [truncated]"
    return message, False
