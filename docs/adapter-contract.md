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

```python
envelope = search_sources(
    "query",
    provider="my-search",
    limit=5,
    adapters={"my-search": MySearch()},
)
```

## Browser and device adapters

A future browser or Android adapter should be a separate process with an allowlisted executable,
explicit profile/device selection, read-only navigation by default, output byte caps, and an audit
record of every navigation. A screenshot or accessibility dump is an adapter artifact, not proof
of source truth. Login, clicks, forms, uploads, posting, purchases, and CAPTCHA handling require a
different state-changing authorization contract and are outside v0.1.
