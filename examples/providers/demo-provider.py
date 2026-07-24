#!/usr/bin/env python3
from __future__ import annotations

import json
import sys


def main() -> int:
    request = json.load(sys.stdin)
    query = " ".join(str(request.get("query") or "").split())
    limit = int(request.get("limit") or 0)
    candidates = []
    if query and limit > 0:
        candidates.append(
            {
                "title": f"Protocol example for {query}",
                "locator": "https://github.com/estelledc/tracefetch",
                "snippet": "Deterministic example output; not a live search result.",
                "metadata": {"fixture": True},
            }
        )
    response = {
        "schema_version": "tracefetch.provider-response.v1",
        "provider": "demo",
        "scope": "demo",
        "candidates": candidates,
        "warnings": ["demo provider does not perform live discovery"],
    }
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
