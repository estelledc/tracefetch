from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from tracefetch.contracts import DoctorCheck, DoctorEnvelope, ProviderManifest
from tracefetch.providers import provider_availability


def run_doctor(
    *,
    provider_manifest: ProviderManifest | None = None,
    provider_base_dir: Path | None = None,
    root: Path | None = None,
) -> DoctorEnvelope:
    workspace = (root or Path.cwd()).expanduser().resolve()
    rg = shutil.which("rg")
    checks = [
        DoctorCheck(
            name="python",
            status="ok" if sys.version_info >= (3, 11) else "off",
            detail=sys.version.split()[0],
            capability="runtime",
        ),
        _command_check("gh", "github-search"),
        _command_check("mcporter", "exa-search"),
        _command_check("markitdown", "document-normalization"),
        _command_check("agent-reach", "platform-routing"),
        DoctorCheck(
            name="workspace",
            status="ok" if workspace.is_dir() else "off",
            detail=(
                f"ready via {Path(rg).name if rg else 'bounded Python fallback'}"
                if workspace.is_dir()
                else "root is not a directory"
            ),
            capability="local-search",
        ),
        DoctorCheck(
            name="firecrawl",
            status="ok" if os.environ.get("FIRECRAWL_API_KEY") else "off",
            detail=(
                "FIRECRAWL_API_KEY configured"
                if os.environ.get("FIRECRAWL_API_KEY")
                else "FIRECRAWL_API_KEY missing"
            ),
            capability="remote-rendered-reader",
        ),
    ]
    base_dir = (provider_base_dir or Path.cwd()).resolve()
    for spec in (provider_manifest or ProviderManifest()).providers:
        available, backend = provider_availability(spec, base_dir=base_dir)
        checks.append(
            DoctorCheck(
                name=f"provider:{spec.name}",
                status="ok" if available else "off",
                detail=backend,
                capability=f"search-scope:{spec.scope}",
            )
        )
    return DoctorEnvelope(checks=checks)


def _command_check(command: str, capability: str) -> DoctorCheck:
    path = shutil.which(command)
    return DoctorCheck(
        name=command,
        status="ok" if path else "off",
        detail=Path(path).name if path else "not installed",
        capability=capability,
    )
