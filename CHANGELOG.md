# Changelog

All notable changes are documented here.

## Unreleased

- Add bounded GitHub query relaxation when a long natural-language repository query returns no
  candidates, with the actual query variant recorded on each result.
- Add per-provider `candidate_count` observability to distinguish an empty successful search from
  a provider failure.
- Compact Exa highlights to 1,200 characters, remove separator noise, and mark truncation
  explicitly.
- Add a reproducible four-query `intern-journal` dogfood suite and before/after report.
- Classify upstream HTTP 429 failures as `rate_limited` and bound provider diagnostics to 320
  characters instead of forwarding tool stacks or credential setup examples.
- Remove embedded XML processing instructions and empty icon-only permalinks during HTML
  normalization so headings and anchors remain clean.
- Add the consumer-side Agent Reach/TraceFetch integration contract used by `intern-journal`.

## 0.1.0 - 2026-07-24

- Add Exa and GitHub discovery with deterministic multi-provider merging.
- Add policy-gated direct, Jina, and Firecrawl readers.
- Add HTML/JSON/text normalization and optional MarkItDown document conversion.
- Add atomic provenance bundles, line anchors, strict receipts, and local verification.
- Add bounded same-origin crawl state, retry classification, policy pinning, and resume.
- Add programmatic reader/search adapter injection and `doctor` capability reporting.
- Add security, architecture, research, and `intern-journal` integration documentation.
