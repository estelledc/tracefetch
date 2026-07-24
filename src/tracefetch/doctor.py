from __future__ import annotations

import importlib.util
import os
import shutil
import sys

from tracefetch.adapters.search import ExaSearchProvider, GitHubSearchProvider
from tracefetch.contracts import DoctorCheck, DoctorEnvelope


def run_doctor() -> DoctorEnvelope:
    crawl4ai_present = importlib.util.find_spec("crawl4ai") is not None
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
        _command_check("scrcpy", "android-capture"),
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
        DoctorCheck(
            name="crawl4ai",
            status="ok" if crawl4ai_present else "off",
            detail="Python package available" if crawl4ai_present else "not installed",
            capability="local-rendered-reader-adapter-candidate",
        ),
    ]
    ExaSearchProvider().available()
    GitHubSearchProvider().available()
    return DoctorEnvelope(checks=checks)


def _command_check(command: str, capability: str) -> DoctorCheck:
    path = shutil.which(command)
    return DoctorCheck(
        name=command,
        status="ok" if path else "off",
        detail=path or "not installed",
        capability=capability,
    )
