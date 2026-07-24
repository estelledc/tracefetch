from __future__ import annotations

import json
import os
import re
import shutil

# Workspace discovery uses fixed argv arrays for ripgrep/git and never invokes a shell.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from tracefetch.contracts import SearchResultAttempt, SearchResultCandidate
from tracefetch.errors import InvalidInputError

DEFAULT_GLOBS = (
    "*.md",
    "*.txt",
    "*.rst",
    "*.py",
    "*.js",
    "*.jsx",
    "*.ts",
    "*.tsx",
    "*.swift",
    "*.kt",
    "*.java",
    "*.go",
    "*.rs",
    "*.c",
    "*.h",
    "*.cpp",
    "*.hpp",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.sh",
    "*.bash",
    "*.zsh",
    "*.html",
    "*.css",
    "*.sql",
    "*.proto",
)
EXCLUDED_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "vendor",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "how",
    "in",
    "of",
    "the",
    "to",
    "what",
    "with",
    "如何",
    "什么",
    "怎么",
    "一个",
}
LOW_SIGNAL_TERMS = {"ai", "code", "project", "search", "tool"}
MAX_FILE_BYTES = 1_000_000
MAX_FALLBACK_FILES = 20_000


def search_workspace(
    root: Path,
    query: str,
    *,
    limit: int,
    include_globs: list[str] | None = None,
) -> tuple[list[SearchResultCandidate], SearchResultAttempt, list[str]]:
    workspace = root.expanduser().resolve()
    if not workspace.is_dir():
        raise InvalidInputError("workspace search root must be an existing directory")
    tokens = _query_tokens(query)
    globs = tuple(include_globs or DEFAULT_GLOBS)
    executable = shutil.which("rg")
    if executable:
        matches, variant, relaxed, error = _search_with_rg(
            executable,
            workspace,
            query,
            tokens,
            globs,
        )
        if error is None:
            candidates = _to_candidates(matches[:limit], variant=variant, relaxed=relaxed)
            return (
                candidates,
                SearchResultAttempt(
                    provider="workspace",
                    scope="local",
                    status="success",
                    candidate_count=len(candidates),
                    backend="ripgrep",
                    metadata={"query_variant": variant, "query_relaxed": relaxed},
                ),
                [],
            )

    matches, variant, relaxed, used_git = _search_with_python(
        workspace,
        query,
        tokens,
        globs,
    )
    candidates = _to_candidates(matches[:limit], variant=variant, relaxed=relaxed)
    warnings = []
    if executable:
        warnings.append("ripgrep failed; used the bounded Python workspace scanner")
    elif not used_git:
        warnings.append(
            "ripgrep and Git file enumeration were unavailable; fallback ignore handling is limited"
        )
    return (
        candidates,
        SearchResultAttempt(
            provider="workspace",
            scope="local",
            status="success",
            candidate_count=len(candidates),
            backend="python-fallback",
            metadata={"query_variant": variant, "query_relaxed": relaxed},
        ),
        warnings,
    )


def _search_with_rg(
    executable: str,
    root: Path,
    query: str,
    tokens: list[str],
    globs: tuple[str, ...],
) -> tuple[list[dict[str, Any]], str, bool, str | None]:
    variants: list[tuple[str, bool, bool]] = [(query, False, True)]
    relaxed = _relaxed_pattern(tokens)
    if relaxed:
        variants.append((relaxed, True, False))
    selected_variant = query
    selected_relaxed = False
    for variant, is_relaxed, fixed in variants:
        command = [
            executable,
            "--json",
            "--line-number",
            "--smart-case",
            "--max-count",
            "8",
            "--hidden",
        ]
        if fixed:
            command.append("--fixed-strings")
        for glob in globs:
            command.extend(["--glob", glob])
        for directory in sorted(EXCLUDED_DIRECTORIES):
            command.extend(["--glob", f"!**/{directory}/**"])
        command.extend([variant, "."])
        completed = _run(command, cwd=root, timeout=45)
        if completed.returncode not in {0, 1}:
            return [], selected_variant, selected_relaxed, "ripgrep_failed"
        matches = _parse_rg_json(completed.stdout, root, query, tokens)
        selected_variant = variant
        selected_relaxed = is_relaxed
        if matches:
            return matches, selected_variant, selected_relaxed, None
    return [], selected_variant, selected_relaxed, None


def _parse_rg_json(
    raw: str,
    root: Path,
    query: str,
    tokens: list[str],
) -> list[dict[str, Any]]:
    matches_by_path: dict[str, list[dict[str, Any]]] = {}
    for line in raw.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "match" or not isinstance(payload.get("data"), dict):
            continue
        data = payload["data"]
        path_text = _nested_text(data.get("path"))
        snippet = _nested_text(data.get("lines"))
        line_number = data.get("line_number")
        if not path_text or not snippet or not isinstance(line_number, int):
            continue
        candidate_path = root / path_text
        relative = _safe_relative(candidate_path, root)
        if relative is None:
            continue
        matches_by_path.setdefault(relative, []).append(
            {"line_number": line_number, "snippet": snippet.strip()}
        )
    return _rank_matches(matches_by_path, query, tokens)


def _search_with_python(
    root: Path,
    query: str,
    tokens: list[str],
    globs: tuple[str, ...],
) -> tuple[list[dict[str, Any]], str, bool, bool]:
    files, used_git = _workspace_files(root, globs)
    variants: list[tuple[str, bool]] = [(query.casefold(), False)]
    if len(tokens) >= 2:
        variants.append(("", True))
    selected_variant = query
    selected_relaxed = False
    for phrase, relaxed in variants:
        matches_by_path: dict[str, list[dict[str, Any]]] = {}
        for path in files:
            try:
                if path.stat().st_size > MAX_FILE_BYTES or path.is_symlink():
                    continue
                relative = _safe_relative(path, root)
                if relative is None:
                    continue
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(),
                    start=1,
                ):
                    folded = line.casefold()
                    matched = (
                        phrase in folded
                        if not relaxed
                        else any(token.casefold() in folded for token in tokens)
                    )
                    if not matched:
                        continue
                    matches_by_path.setdefault(relative, []).append(
                        {"line_number": line_number, "snippet": line.strip()}
                    )
                    if len(matches_by_path[relative]) >= 8:
                        break
            except OSError:
                continue
        selected_relaxed = relaxed
        selected_variant = (_relaxed_pattern(tokens) or query) if relaxed else query
        if matches_by_path:
            return (
                _rank_matches(matches_by_path, query, tokens),
                selected_variant or query,
                selected_relaxed,
                used_git,
            )
    return [], selected_variant or query, selected_relaxed, used_git


def _workspace_files(root: Path, globs: tuple[str, ...]) -> tuple[list[Path], bool]:
    git = shutil.which("git")
    if git:
        completed = _run(
            [git, "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            timeout=30,
        )
        if completed.returncode == 0:
            paths = [root / item for item in completed.stdout.split("\0") if item]
            return [path for path in paths if _matches_any(path.name, globs)], True
    files: list[Path] = []
    for current_root, directories, names in os.walk(root, followlinks=False):
        directories[:] = [item for item in directories if item not in EXCLUDED_DIRECTORIES]
        for name in names:
            if _matches_any(name, globs):
                files.append(Path(current_root) / name)
                if len(files) >= MAX_FALLBACK_FILES:
                    return files, False
    return files, False


def _rank_matches(
    matches_by_path: dict[str, list[dict[str, Any]]],
    query: str,
    tokens: list[str],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    normalized_query = query.casefold()
    for path, matches in matches_by_path.items():
        combined = " ".join(str(item["snippet"]) for item in matches).casefold()
        path_folded = path.casefold()
        matched_terms = [token for token in tokens if token.casefold() in combined]
        path_terms = [token for token in tokens if token.casefold() in path_folded]
        weighted_coverage = sum(_term_weight(token) for token in matched_terms)
        exact_phrase = normalized_query in combined

        def line_weight(item: dict[str, Any]) -> float:
            folded = str(item["snippet"]).casefold()
            return sum(_term_weight(token) for token in tokens if token.casefold() in folded)

        best_match = max(matches, key=line_weight)
        best_line_weight = line_weight(best_match)
        score = (
            weighted_coverage * 10
            + best_line_weight * 2
            + len(path_terms) * 3
            + (100 if exact_phrase else 0)
        )
        ranked.append(
            {
                "path": path,
                "line_number": best_match["line_number"],
                "snippet": best_match["snippet"],
                "matched_terms": matched_terms,
                "term_coverage": round(len(matched_terms) / len(tokens), 3) if tokens else 0.0,
                "relevance_score": round(score, 2),
                "best_line_weight": round(best_line_weight, 2),
                "matched_line_count": len(matches),
            }
        )
    ranked.sort(key=lambda item: (-float(item["relevance_score"]), str(item["path"])))
    return ranked


def _to_candidates(
    matches: list[dict[str, Any]],
    *,
    variant: str,
    relaxed: bool,
) -> list[SearchResultCandidate]:
    candidates: list[SearchResultCandidate] = []
    for rank, item in enumerate(matches, start=1):
        path = str(item["path"])
        line_number = int(item["line_number"])
        candidates.append(
            SearchResultCandidate(
                rank=rank,
                title=path,
                locator=f"{path}:{line_number}",
                snippet=_compact_text(str(item["snippet"]), 500),
                provider="workspace",
                scope="local",
                source_class="workspace-source",
                evidence_state="local-source-match",
                sensitivity="project",
                metadata={
                    "line_number": line_number,
                    "query_variant": variant,
                    "query_relaxed": relaxed,
                    "matched_terms": item["matched_terms"],
                    "term_coverage": item["term_coverage"],
                    "relevance_score": item["relevance_score"],
                    "best_line_weight": item["best_line_weight"],
                    "matched_line_count": item["matched_line_count"],
                },
            )
        )
    return candidates


def _query_tokens(query: str) -> list[str]:
    tokens = [
        token
        for token in re.findall(r"[\w@.-]+", query, flags=re.UNICODE)
        if token.casefold() not in STOPWORDS and len(token) > 1
    ]
    return list(dict.fromkeys(tokens))[:12]


def _relaxed_pattern(tokens: list[str]) -> str | None:
    if len(tokens) < 2:
        return None
    return "|".join(re.escape(token) for token in tokens[:6])


def _term_weight(token: str) -> float:
    return 0.25 if token.casefold() in LOW_SIGNAL_TERMS else 1.0


def _nested_text(value: Any) -> str:
    return str(value.get("text") or "") if isinstance(value, dict) else ""


def _safe_relative(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _matches_any(name: str, globs: tuple[str, ...]) -> bool:
    return any(Path(name).match(pattern) for pattern in globs)


def _compact_text(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    marker = " [truncated]"
    return compact[: limit - len(marker)].rstrip() + marker


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # nosec B603
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 124, "", type(exc).__name__)
