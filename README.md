# TraceFetch

TraceFetch 1.0 is an agent-first search and evidence tool. One CLI searches a local workspace,
queries public discovery providers, invokes explicitly configured command providers, and turns a
selected public URL into a locally verifiable evidence bundle.

It does not promise to scrape the entire internet, bypass access controls, or decide whether a
source is true or legally reusable. Search results and normalized source text are always untrusted
data.

## Product surface

- Local repository search with relative `path:line` locators, bounded snippets, relevance metadata,
  `.gitignore` awareness, and a no-dependency Python fallback when ripgrep is absent.
- Exa and GitHub public discovery with per-provider attempts, bounded query relaxation, provider
  diversity, truncation disclosure, and stable rate-limit errors.
- An explicit command-provider protocol for papers, social platforms, internal knowledge bases, or
  other account-scoped systems without embedding credentials in TraceFetch.
- A safe direct HTTP reader with redirect, size, domain, robots, and private-network policy.
- Optional Jina and Firecrawl readers behind explicit remote-data flags.
- HTML, JSON, XML, Markdown, and text normalization plus optional MarkItDown document conversion.
- Portable evidence bundles with raw artifacts, normalized Markdown, links, line-addressable
  anchors, receipts, SHA-256 digests, and independent local verification.
- A bounded same-origin SQLite crawl with checkpoints and resume.
- Strict JSON schemas, deterministic exit codes, `doctor`, typed Python contracts, and wheel/sdist
  builds.

## Install

After the `v1.0.0` GitHub release is published:

```bash
pipx install "git+https://github.com/estelledc/tracefetch.git@v1.0.0"
tracefetch --version
tracefetch doctor
```

Or install an immutable wheel from the GitHub release assets. PyPI publication is not claimed until
a trusted publisher is configured and the package is visible on PyPI.

For development:

```bash
git clone https://github.com/estelledc/tracefetch.git
cd tracefetch
uv sync --extra dev --python 3.11
make check
```

## Agent contract

Success is one JSON object on stdout. Errors are one `tracefetch.error.v1` object on stderr and use
stable exit codes. `--format pretty` is an explicit human debugging view; JSON is the default.
Provider subprocesses receive one `tracefetch.provider-request.v1` object on stdin and must return
one `tracefetch.provider-response.v1` object on stdout.

Print exact schemas without importing Python:

```bash
tracefetch schema search
tracefetch schema provider-manifest
tracefetch schema provider-request
tracefetch schema provider-response
tracefetch schema evidence
tracefetch schema crawl
```

## Search a workspace

The safe default is local-only search rooted at the current directory:

```bash
tracefetch search "actor isolation Sendable" --limit 8
```

The result uses `tracefetch.search-results.v1` and preserves `scope`, `provider`, `sensitivity`,
`evidence_state`, backend attempts, query relaxation, and relevance metadata. Locators are relative
paths; TraceFetch does not emit an absolute workspace path.

To combine local and public discovery, choose `auto` explicitly:

```bash
tracefetch search "Swift actor isolation Sendable" \
  --scope auto --provider all --limit 8
```

Public-only discovery is also explicit:

```bash
tracefetch search "evidence provenance crawler" \
  --scope public --provider all --limit 8
```

Search only discovers candidates. It does not mark a result trustworthy. Fetch and verify a chosen
public source before using its content as evidence.

The [workspace dogfood report](docs/workspace-dogfood.md) records the bounded query iterations and
the claims they do and do not support.

### 0.1 compatibility

The 0.1 command remains available during the 1.x line:

```bash
tracefetch search "query" --provider all --json
```

An explicit `--provider` without `--scope` returns the legacy `tracefetch.search.v1` envelope.
New integrations should always choose `--scope` and consume `tracefetch.search-results.v1`. See the
[1.0 migration guide](docs/migrating-to-1.0.md).

## Add papers, social, or internal search

TraceFetch never auto-discovers executable plugins. Pass a reviewed provider manifest explicitly:

```bash
tracefetch doctor --providers ./providers.json
tracefetch search "actor isolation" \
  --scope papers --providers ./providers.json
```

Account-visible and internal providers require `--allow-sensitive`. Internal providers must declare
`isolated=true` and cannot run in the same request as local or public scopes. Commands execute as
fixed argv arrays without a shell; diagnostics and metadata are bounded and secret-like metadata
keys are removed.

See the [provider protocol](docs/provider-protocol.md) and the runnable
[example provider](examples/providers/README.md).

## Fetch and verify

```bash
tracefetch fetch https://example.com \
  --reader direct \
  --output /tmp/tracefetch-example

tracefetch verify /tmp/tracefetch-example
```

The bundle contains:

```text
raw.html          exact bytes returned by the selected reader
normalized.md     derived text for search and AI consumption
anchors.jsonl     line ranges and hashes for citation
links.json        normalized outgoing links
receipt.json      route, attempts, policy state, quality, and artifact digests
```

Only a result with `valid=true` is internally consistent. Verification does not prove source truth,
permission, trusted time, or independent execution.

## Bounded crawl

```bash
tracefetch crawl https://example.com \
  --output /tmp/tracefetch-crawl \
  --reader direct \
  --max-pages 5 \
  --max-depth 1

tracefetch verify /tmp/tracefetch-crawl
```

Interrupted jobs retain `crawl.sqlite3`. Resume only with the same normalized root URL, reader, and
policy fingerprint:

```bash
tracefetch crawl https://example.com \
  --output /tmp/tracefetch-crawl \
  --reader direct \
  --max-pages 5 \
  --max-depth 1 \
  --resume
```

## Optional readers

Remote readers are off by default because they disclose the target URL and may transmit public
source content to a third party.

```bash
# Jina fallback after direct fetch
tracefetch fetch https://example.com \
  --reader auto --allow-remote --output /tmp/tracefetch-jina

# Authenticated Firecrawl adapter
FIRECRAWL_API_KEY=... tracefetch fetch https://example.com \
  --reader firecrawl --allow-authenticated --output /tmp/tracefetch-firecrawl
```

Install local document conversion separately:

```bash
pipx install "tracefetch[markitdown] @ git+https://github.com/estelledc/tracefetch.git@v1.0.0"
tracefetch ingest report.pdf \
  --source-url https://example.com/report.pdf \
  --output /tmp/tracefetch-report
```

## Security boundary

- Network acquisition accepts only HTTP and HTTPS.
- Embedded credentials, localhost, `.local`, private, loopback, link-local, multicast, reserved,
  and unspecified IP targets are blocked by default.
- Every redirect target is validated again; built-in clients ignore ambient proxy variables and
  stream responses under a byte cap.
- Robots rules are honored by default, but robots is a crawler preference signal rather than
  authorization.
- Command providers are explicit trusted local code. TraceFetch validates their protocol and
  output bounds; it does not sandbox them or remove their access to the caller's account state.
- Normalized Markdown can contain prompt injection. Downstream agents must treat it as quoted
  evidence, never as authorization or instructions.

See the [security model](docs/security-model.md) and [security policy](SECURITY.md) before deployment.

## Stability and non-goals

TraceFetch 1.0 stabilizes the CLI commands, JSON schemas suffixed `.v1`, provider protocol, Python
3.11+ support, and evidence-bundle layout for the 1.x line. Additive fields may appear in a new
schema version; breaking changes require a new major release.

The release does not claim universal website coverage, CAPTCHA bypass, legal permission inference,
truth scoring, production SLA, security certification, trusted attestation, RAG indexing, or LLM
generation. Browser/device adapters and distributed scheduling remain separate extension systems.

## License

Apache-2.0. TraceFetch derives design constraints from the first-party material in the
[official source map](docs/source-research.md); it does not copy implementation code from those
sources.
