from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tracefetch.contracts import (
    CrawlReceipt,
    DoctorEnvelope,
    ErrorEnvelope,
    EvidenceReceipt,
    ProviderManifest,
    ProviderRequest,
    ProviderResponse,
    SearchEnvelope,
    SearchResultsEnvelope,
)

MODELS: dict[str, Any] = {
    "crawl.schema.json": CrawlReceipt,
    "doctor.schema.json": DoctorEnvelope,
    "error.schema.json": ErrorEnvelope,
    "evidence.schema.json": EvidenceReceipt,
    "provider-manifest.schema.json": ProviderManifest,
    "provider-request.schema.json": ProviderRequest,
    "provider-response.schema.json": ProviderResponse,
    "provider-search.schema.json": SearchEnvelope,
    "search.schema.json": SearchResultsEnvelope,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "schemas"
    root.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    for name, model in MODELS.items():
        expected = (
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
        path = root / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                drift.append(name)
        else:
            path.write_text(expected, encoding="utf-8")
    if drift:
        print("schema drift: " + ", ".join(drift))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
