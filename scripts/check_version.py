from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check TraceFetch release version coherence")
    parser.add_argument("--tag")
    args = parser.parse_args()

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = str(project["version"])
    init_text = (ROOT / "src/tracefetch/__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    failures: list[str] = []
    if not SEMVER.fullmatch(version):
        failures.append("pyproject version is not MAJOR.MINOR.PATCH")
    if init_match is None or init_match.group(1) != version:
        failures.append("package __version__ does not match pyproject")
    if not re.search(
        rf"^## {re.escape(version)} - [0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$", changelog, re.MULTILINE
    ):
        failures.append("CHANGELOG has no dated section for the package version")
    if args.tag and args.tag.removeprefix("v") != version:
        failures.append("Git tag does not match the package version")

    payload = {"schema_version": "tracefetch.version-check.v1", "version": version}
    if failures:
        payload.update({"status": "failed", "failures": failures})
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1
    payload.update({"status": "ok", "failures": []})
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
