# Adapter contract

Adapters acquire candidate URLs or source bytes. They do not write bundles, choose trust, or mark
content as true. The core owns policy evidence, normalization, atomic publication, and verification.

## Reader adapter

A reader implements the protocol in `tracefetch.adapters.base`:

```python
class ReaderAdapter(Protocol):
    name: str
    remote: bool
    authenticated: bool

    def available(self) -> tuple[bool, str]: ...
    def fetch(self, url: str, policy: Policy) -> ReaderResult: ...
```

`ReaderResult` must provide:

- adapter and requested/final URL;
- HTTP-like status and content type;
- bytes returned by that adapter;
- timezone-aware acquisition time;
- robots state;
- optional Markdown and non-secret metadata.

Required invariants:

- Validate the target and every redirect with the supplied policy.
- Apply size and timeout limits before buffering unbounded content.
- Never place tokens, cookies, authorization headers, signed URLs, or full unsafe response headers
  in metadata.
- Set `remote` when a third party receives the URL/content, even if the service is unauthenticated.
- Set `authenticated` when the adapter uses a credential.
- Raise a `TraceFetchError` subtype with a stable retryability classification.
- Do not claim origin-exact bytes when a service rendered, transformed, or reconstructed them.

Python callers inject a reader explicitly:

```python
bundle = fetch_to_bundle(
    url,
    output,
    reader="my-reader",
    policy=policy,
    adapters={"my-reader": MyReader()},
)
```

Built-in names are reserved. Custom readers are never added to the CLI's `auto` route implicitly.

The portable integration surface remains the CLI and bundle files. Success JSON is written to
stdout; errors are written to stderr with schema `tracefetch.error.v1`. Exit codes distinguish
invalid input (2), policy block (3), unavailable adapter (4), fetch failure (5), and verification
failure (6). Consumers can print the exact contracts without Python imports:

```bash
tracefetch schema evidence
tracefetch schema search
tracefetch schema crawl
```

## Search provider

A provider implements `name`, `available()`, and `search(query, limit)`. Results are candidate
records, not evidence. Providers must preserve their own provenance name and return stable errors.
`provider=all` queries every available provider and round-robin merges deduplicated URLs so the
first backend cannot monopolize the result limit.

Each attempt reports `candidate_count`, including successful zero-result calls. The built-in
GitHub provider first submits the exact query. Only when that returns no repositories, it may make
one bounded fallback from the first two non-generic topic terms and sort that broader result by
stars. Every fallback candidate records the actual `query_variant` and `query_relaxed=true`.
Callers must still judge relevance; query relaxation improves recall and can reduce precision.
Provider process failures are compacted to at most 320 characters. HTTP 429/rate-limit responses
use the stable `rate_limited` code; stack traces and credential setup examples are not forwarded
into the search envelope.

```python
envelope = search_sources(
    "query",
    provider="my-search",
    limit=5,
    adapters={"my-search": MySearch()},
)
```

The Python adapter API remains the low-level 0.1 compatibility surface. The 1.0 CLI uses
`tracefetch.search-results.v1` and adds local scopes plus explicit process providers. See the
[command provider protocol](provider-protocol.md) for the portable extension boundary.

## Browser and device adapters

A future browser or Android adapter should be a separate process with an allowlisted executable,
explicit profile/device selection, read-only navigation by default, output byte caps, and an audit
record of every navigation. A screenshot or accessibility dump is an adapter artifact, not proof
of source truth. Login, clicks, forms, uploads, posting, purchases, and CAPTCHA handling require a
different state-changing authorization contract and are outside TraceFetch 1.x.
