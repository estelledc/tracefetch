# Contributing

TraceFetch accepts focused changes that preserve its evidence and safety boundaries.

## Setup and gates

```bash
uv sync --extra dev --python 3.11
make check
```

`make check` runs formatting, lint, strict type checking, tests with at least 80% coverage, wheel and
source-distribution builds, and whitespace validation when the tree is a Git repository.

## Change rules

- Add or update a test for every behavior or contract change.
- Keep raw acquisition separate from normalized/derived output.
- Never weaken URL, redirect, path, byte, robots, or remote-data policy silently.
- New remote/authenticated adapters must disclose those properties in receipts.
- Do not add CAPTCHA bypass, credential harvesting, stealth fingerprinting, or default access-control
  circumvention.
- Do not copy source from projects in the research matrix. Record design provenance and comply with
  each optional dependency's license.
- Contract changes require a schema-version decision and regenerated files in `schemas/`.
- Search CLI changes must preserve the `tracefetch.search-results.v1` contract or explicitly target
  the next major release. The 0.1 `--provider` compatibility path remains supported through 1.x.
- Command providers must use fixed argv, explicit manifests, bounded JSON, sensitivity labels, and
  internal-scope isolation; they are trusted local processes, not sandboxed extensions.
- Keep errors machine-readable and avoid secrets in messages, metadata, fixtures, and snapshots.

## Adapter review checklist

An adapter contribution must document availability detection, data sent off-machine, credentials,
redirect behavior, byte and timeout limits, robots handling, final URL semantics, retryability, and
which bytes are exact versus transformed. Browser or device adapters additionally need a separate
authorization model for state-changing actions.
