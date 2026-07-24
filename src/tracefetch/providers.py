from __future__ import annotations

import json
import os
import re
import shutil

# Explicit provider manifests supply fixed argv arrays; shell execution is never used.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tracefetch.contracts import (
    CommandProviderSpec,
    ProviderCandidate,
    ProviderManifest,
    ProviderRequest,
    ProviderResponse,
)
from tracefetch.errors import (
    AdapterUnavailableError,
    InvalidInputError,
    ProviderProtocolError,
    SearchFailedError,
)

MAX_MANIFEST_BYTES = 1_000_000
MAX_PROVIDER_OUTPUT_BYTES = 2_000_000
MAX_PROVIDER_ERROR_CHARS = 320
SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|secret|token|api[_-]?key)",
    re.IGNORECASE,
)


def load_provider_manifest(path: Path | None) -> tuple[ProviderManifest, Path]:
    """Load one explicit command-provider manifest and return its working directory."""

    if path is None:
        return ProviderManifest(), Path.cwd()
    manifest_path = path.expanduser().resolve()
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise InvalidInputError("provider manifest exceeds the 1 MB limit")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = ProviderManifest.model_validate(payload)
    except InvalidInputError:
        raise
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise InvalidInputError(
            "invalid provider manifest",
            details={"reason": type(exc).__name__},
        ) from exc

    names: set[str] = set()
    for provider in manifest.providers:
        if provider.name in names:
            raise InvalidInputError(f"duplicate command provider: {provider.name}")
        names.add(provider.name)
        if provider.name in {"workspace", "exa", "github"}:
            raise InvalidInputError(f"command provider name is reserved: {provider.name}")
        if provider.scope in {"auto", "local", "public"}:
            raise InvalidInputError(f"command provider scope is reserved: {provider.scope}")
        if provider.sensitivity == "internal" and not provider.isolated:
            raise InvalidInputError(
                f"internal provider {provider.name!r} must declare isolated=true"
            )
        if any(not part or "\x00" in part for part in provider.command):
            raise InvalidInputError(f"provider {provider.name!r} has an invalid command")
    return manifest, manifest_path.parent


def provider_availability(
    spec: CommandProviderSpec,
    *,
    base_dir: Path,
) -> tuple[bool, str]:
    try:
        resolved = resolve_provider_command(spec, base_dir=base_dir)
    except AdapterUnavailableError as exc:
        return False, exc.message
    return True, Path(resolved[0]).name


def resolve_provider_command(
    spec: CommandProviderSpec,
    *,
    base_dir: Path,
) -> list[str]:
    command = list(spec.command)
    executable = command[0]
    if "/" in executable or os.sep in executable:
        candidate = Path(executable).expanduser()
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        candidate = candidate.resolve()
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise AdapterUnavailableError(f"provider executable is unavailable: {spec.name}")
        command[0] = str(candidate)
        return command
    resolved = shutil.which(executable)
    if resolved is None:
        raise AdapterUnavailableError(f"provider executable is unavailable: {spec.name}")
    command[0] = resolved
    return command


def run_command_provider(
    spec: CommandProviderSpec,
    *,
    query: str,
    limit: int,
    base_dir: Path,
) -> ProviderResponse:
    command = resolve_provider_command(spec, base_dir=base_dir)
    request = ProviderRequest(query=query, limit=limit)
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("COV_CORE_") or key == "COVERAGE_PROCESS_START":
            environment.pop(key, None)
    try:
        completed = subprocess.run(  # nosec B603
            command,
            cwd=base_dir,
            env=environment,
            input=request.model_dump_json(),
            capture_output=True,
            text=True,
            timeout=spec.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SearchFailedError(
            f"provider {spec.name!r} timed out",
            details={"provider": spec.name},
        ) from exc
    except OSError as exc:
        raise AdapterUnavailableError(f"provider {spec.name!r} could not be executed") from exc

    if completed.returncode != 0:
        raise SearchFailedError(
            f"provider {spec.name!r} failed",
            details={
                "provider": spec.name,
                "returncode": completed.returncode,
                "diagnostic": _compact_provider_error(completed.stderr),
            },
        )
    if len(completed.stdout.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
        raise ProviderProtocolError(f"provider {spec.name!r} exceeded the 2 MB output limit")
    try:
        response = ProviderResponse.model_validate_json(completed.stdout)
    except ValidationError as exc:
        raise ProviderProtocolError(
            f"provider {spec.name!r} returned an invalid response",
            details={"provider": spec.name},
        ) from exc
    if response.provider != spec.name or response.scope != spec.scope:
        raise ProviderProtocolError(
            f"provider {spec.name!r} response identity does not match its manifest"
        )
    cleaned = [
        ProviderCandidate(
            title=_compact_text(candidate.title, 300),
            locator=_compact_text(candidate.locator, 2_000),
            snippet=_compact_text(candidate.snippet, 1_600),
            published_at=_compact_optional(candidate.published_at, 100),
            metadata=_safe_metadata(candidate.metadata),
        )
        for candidate in response.candidates[:limit]
    ]
    return response.model_copy(
        update={
            "candidates": cleaned,
            "warnings": [_compact_text(item, 500) for item in response.warnings[:20]],
        }
    )


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, item in list(value.items())[:32]:
        if SENSITIVE_KEY.search(str(key)):
            continue
        cleaned[str(key)[:100]] = _safe_value(item, depth=0)
    return cleaned


def _safe_value(value: Any, *, depth: int) -> Any:
    if depth >= 2:
        return _compact_text(str(value), 500)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _compact_text(value, 1_000)
    if isinstance(value, list):
        return [_safe_value(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _safe_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
            if not SENSITIVE_KEY.search(str(key))
        }
    return _compact_text(str(value), 500)


def _compact_provider_error(raw: str) -> str:
    plain = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    plain = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", plain)
    plain = re.sub(
        r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)[^\s,;]+",
        r"\1[redacted]",
        plain,
    )
    return _compact_text(plain or "provider exited without a diagnostic", MAX_PROVIDER_ERROR_CHARS)


def _compact_optional(value: str | None, limit: int) -> str | None:
    return _compact_text(value, limit) if value else None


def _compact_text(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    marker = " [truncated]"
    return compact[: limit - len(marker)].rstrip() + marker
