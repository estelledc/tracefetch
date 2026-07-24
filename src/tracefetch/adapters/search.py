from __future__ import annotations

import json
import re
import shutil

# Subprocesses use fixed argv, no shell, and executables resolved by shutil.which.
import subprocess  # nosec B404
from typing import Any

from tracefetch.contracts import SearchCandidate
from tracefetch.errors import AdapterUnavailableError, FetchFailedError


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
            raise FetchFailedError(
                completed.stderr.strip() or "Exa search failed",
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
        try:
            completed = subprocess.run(  # nosec B603
                [
                    executable,
                    "search",
                    "repos",
                    query,
                    "--limit",
                    str(limit),
                    "--json",
                    "fullName,description,stargazersCount,url,updatedAt,license",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchFailedError(
                f"GitHub search execution failed: {exc}", retryable=True
            ) from exc
        if completed.returncode != 0:
            raise FetchFailedError(
                completed.stderr.strip() or "GitHub search failed",
                retryable=True,
                details={"returncode": completed.returncode},
            )
        try:
            payload: list[dict[str, Any]] = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise FetchFailedError("GitHub search returned invalid JSON", retryable=False) from exc
        return [
            SearchCandidate(
                rank=index,
                title=str(item.get("fullName") or "Untitled repository"),
                url=str(item.get("url") or ""),
                snippet=str(item.get("description") or ""),
                provider=self.name,
                published_at=item.get("updatedAt"),
                metadata={
                    "stars": item.get("stargazersCount"),
                    "license": item.get("license"),
                },
            )
            for index, item in enumerate(payload, start=1)
            if item.get("url")
        ]


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
        snippet = highlights_match.group(1).strip() if highlights_match else ""
        candidates.append(
            SearchCandidate(
                rank=len(candidates) + 1,
                title=title.strip() or "Untitled result",
                url=url_match.group(1).strip(),
                snippet=snippet[:2_000],
                provider="exa",
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
