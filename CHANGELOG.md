# Changelog

All notable changes are documented here.

## Unreleased

## 1.0.0 - 2026-07-24

- Promote TraceFetch from an evidence-acquisition alpha to an agent-first local and public search
  product with the stable `tracefetch.search-results.v1` contract.
- Add bounded workspace search with relative `path:line` locators, relevance metadata,
  `.gitignore` awareness, ripgrep acceleration, and a Python fallback.
- Add the explicit `tracefetch.providers.v1` command-provider protocol for paper, social, account,
  and internal search systems without embedding their credentials or SDKs.
- Make JSON the default CLI output, preserve `--format pretty` for debugging, and return malformed
  argument errors through `tracefetch.error.v1`.
- Require explicit sensitive-provider opt-in and isolation for internal scopes; bound provider
  output, redact diagnostics, and remove secret-like metadata keys.
- Preserve the 0.1 `search --provider ... --json` envelope as a documented 1.x compatibility mode.
- Add version-coherence, clean-install, schema, release workflow, migration, provider-protocol, and
  GitHub Release asset contracts.
- Add bounded GitHub query relaxation when a long natural-language repository query returns no
  candidates, with the actual query variant recorded on each result.
- Add per-provider `candidate_count` observability to distinguish an empty successful search from
  a provider failure.
- Compact Exa highlights to 1,200 characters, remove separator noise, and mark truncation
  explicitly.
- Add a reproducible four-query workspace dogfood suite and before/after report.
- Classify upstream HTTP 429 failures as `rate_limited` and bound provider diagnostics to 320
  characters instead of forwarding tool stacks or credential setup examples.
- Remove embedded XML processing instructions and empty icon-only permalinks during HTML
  normalization so headings and anchors remain clean.
- Add a consumer-side Agent Reach/TraceFetch integration contract.

## 0.1.0 - 2026-07-24

- Add Exa and GitHub discovery with deterministic multi-provider merging.
- Add policy-gated direct, Jina, and Firecrawl readers.
- Add HTML/JSON/text normalization and optional MarkItDown document conversion.
- Add atomic provenance bundles, line anchors, strict receipts, and local verification.
- Add bounded same-origin crawl state, retry classification, policy pinning, and resume.
- Add programmatic reader/search adapter injection and `doctor` capability reporting.
- Add security, architecture, research, and consumer integration documentation.
