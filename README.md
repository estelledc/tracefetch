# TraceFetch

TraceFetch is a local-first control plane for search and source acquisition. It routes existing
search/read tools, normalizes their output, and emits a portable evidence bundle that another
project can verify before trusting or indexing the content.

It does not promise to scrape the entire internet. It does not bypass access controls, CAPTCHAs,
or website terms. Source content is always treated as untrusted data.

## What it provides

- Exa and GitHub discovery adapters.
- A safe direct HTTP reader with redirect, size, domain, robots, and private-network policy.
- Optional Jina and Firecrawl readers behind explicit remote-data flags.
- Built-in HTML, JSON, XML, Markdown, and text normalization.
- Optional MarkItDown conversion for PDF and Office files.
- Recorded raw artifacts, normalized Markdown, links, line-addressable anchors, and SHA-256
  receipts.
- A bounded, same-origin SQLite crawl with checkpoints and resume.
- `doctor`, machine-readable schemas, and fail-closed bundle verification.
- Programmatic reader and search-provider injection under explicit, non-reserved names.

## Install for development

```bash
uv sync --extra dev --python 3.11
uv run tracefetch doctor --json
make check
```

## Search

```bash
uv run tracefetch search "evidence provenance crawler" --provider all --limit 5 --json
```

Search only discovers candidates. It does not mark a result trustworthy.

`provider=all` reports each provider's `candidate_count` and round-robin merges results. When a
long GitHub repository query returns zero candidates, TraceFetch makes at most one transparent,
two-topic fallback query; GitHub candidates record `query_variant` and `query_relaxed`. Exa
highlight snippets are compacted to 1,200 characters and carry an explicit truncation marker.

See the [intern-journal dogfood report](docs/intern-journal-dogfood.md) for a real before/after
iteration using four project topics.

## Fetch and verify

```bash
uv run tracefetch fetch https://example.com \
  --reader direct \
  --output /tmp/tracefetch-example \
  --json

uv run tracefetch verify /tmp/tracefetch-example --json
```

The bundle contains:

```text
raw.html          exact bytes returned by the selected reader
normalized.md     derived text for search and AI consumption
anchors.jsonl     line ranges and hashes for citation
links.json        normalized outgoing links
receipt.json      route, attempts, policy state, quality, and artifact digests
```

## Bounded crawl

```bash
uv run tracefetch crawl https://example.com \
  --output /tmp/tracefetch-crawl \
  --reader auto \
  --max-pages 5 \
  --max-depth 1 \
  --json

uv run tracefetch verify /tmp/tracefetch-crawl --json
```

Interrupted jobs retain `crawl.sqlite3`. Continue only with the same normalized root URL, reader
and policy fingerprint; changing limits or remote-adapter flags is rejected:

```bash
uv run tracefetch crawl https://example.com \
  --output /tmp/tracefetch-crawl \
  --reader auto \
  --max-pages 5 \
  --max-depth 1 \
  --resume \
  --json
```

## Optional readers

Remote readers are off by default because they send public source content to a third party.

```bash
# Jina fallback after direct fetch
uv run tracefetch fetch https://example.com \
  --reader auto --allow-remote --output /tmp/tracefetch-jina

# Authenticated Firecrawl adapter
FIRECRAWL_API_KEY=... uv run tracefetch fetch https://example.com \
  --reader firecrawl --allow-authenticated --output /tmp/tracefetch-firecrawl
```

Install local document conversion separately:

```bash
uv sync --extra markitdown
uv run tracefetch ingest report.pdf \
  --source-url https://example.com/report.pdf \
  --output /tmp/tracefetch-report
```

Print the exact JSON contracts without importing Python code:

```bash
uv run tracefetch schema evidence
uv run tracefetch schema search
uv run tracefetch schema crawl
```

## Security boundary

- Only `http` and `https` are accepted for network acquisition.
- Embedded URL credentials, localhost, `.local`, private, loopback, link-local, multicast,
  reserved, and unspecified IP targets are blocked by default.
- Every redirect target is validated again and automatic redirects are disabled in the client.
- Built-in network clients ignore ambient proxy environment variables.
- Responses are streamed under a byte cap.
- Robots rules are honored by default and network/server failures fail closed. Robots is still a
  crawler preference signal, not authorization.
- Remote, authenticated, browser, and device adapters require separate opt-in policies.
- DNS validation reduces SSRF exposure but does not eliminate DNS rebinding or replace network
  egress controls.

See [the security model](docs/security-model.md) before production use.

Verification checks artifact roles and paths, source/raw identity, derived quality counts, anchors,
links and crawl SQLite projection. Receipts always declare themselves unsigned, without trusted
time or independent-execution proof. They detect unsynchronized mutation; they do not prove source
truth, permission to reuse content, or attestation.

## Integration

TraceFetch communicates through JSON and portable bundle files rather than Python imports. See
[the intern-journal integration](examples/intern-journal/README.md) and the
[adapter contract](docs/adapter-contract.md).

Agent Reach remains the installer, platform router, and backend doctor; it intentionally has no
search execution command. A consumer can layer TraceFetch over the Exa and GitHub tools configured
by Agent Reach to gain multi-provider attempts, bounded diagnostics, normalized evidence, and
verification without modifying Agent Reach internals.

The [architecture](docs/architecture.md) explains the trust layers. The
[source research](docs/source-research.md) records which ideas were adopted, deferred, or rejected.

## Status

`0.1.0` is a public-source alpha at
[estelledc/tracefetch](https://github.com/estelledc/tracefetch), not a published package. The direct
reader, local ingestion, evidence contract, verifier, search adapters, and bounded crawl have 90+
offline tests, an 80% coverage gate, schema drift checks, Ruff, strict mypy and wheel/sdist gates.
Browser/device adapters are contract-level extension points, not bundled automation. No production
reliability, standards certification, security certification or universal website coverage is
claimed.

## License

Apache-2.0. TraceFetch derives design constraints from the first-party material in the
[official source map](docs/source-research.md); it does not copy implementation code from those
sources.
