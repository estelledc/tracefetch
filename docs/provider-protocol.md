# Command provider protocol

TraceFetch 1.0 uses an explicit process boundary for search systems that should not become package
dependencies. Examples include paper indexes, social-platform tools, enterprise knowledge bases,
and authenticated search gateways.

## Manifest

The caller passes a manifest with `--providers`; TraceFetch never scans a plugin directory or runs
an undeclared executable.

```json
{
  "schema_version": "tracefetch.providers.v1",
  "providers": [
    {
      "name": "papers",
      "scope": "papers",
      "command": ["./paper-provider"],
      "timeout_seconds": 60,
      "sensitivity": "public",
      "source_class": "paper-candidate",
      "isolated": false
    }
  ]
}
```

Rules:

- `name` and `scope` use lowercase letters, digits, `_`, or `-` and begin with a letter.
- `workspace`, `exa`, and `github` names are reserved. `auto`, `local`, and `public` scopes are
  reserved.
- The command is a fixed argv array. TraceFetch does not invoke a shell or expand variables.
- A path-like executable is resolved relative to the manifest. A bare executable is resolved from
  `PATH`.
- The process inherits the caller's environment because account-aware providers may need their own
  authenticated CLI state. Treat every configured command as trusted local code.
- An `internal` provider must set `isolated=true`. Any provider with `account-visible` or `internal`
  sensitivity requires `--allow-sensitive`.

## Request

TraceFetch writes exactly one compact JSON object to provider stdin and closes the stream:

```json
{
  "schema_version": "tracefetch.provider-request.v1",
  "action": "search",
  "query": "actor isolation",
  "limit": 8
}
```

This is machine input, not an interactive prompt. The provider must not wait for additional user
input.

## Response

The provider writes one JSON object to stdout:

```json
{
  "schema_version": "tracefetch.provider-response.v1",
  "provider": "papers",
  "scope": "papers",
  "candidates": [
    {
      "title": "Structured Concurrency",
      "locator": "https://example.org/paper",
      "snippet": "Candidate abstract",
      "published_at": "2025-01-01",
      "metadata": {
        "citation_count": 12
      }
    }
  ],
  "warnings": []
}
```

The response identity must exactly match the manifest. TraceFetch assigns global ranks and the
manifest-owned scope, source class, evidence state, and sensitivity.

## Bounds and failure semantics

- Timeout: 60 seconds by default, configurable from 0 to 300 seconds.
- Stdout: at most 2 MB.
- Title: 300 characters; locator: 2,000; snippet: 1,600; warnings: 500 each.
- At most 32 metadata keys are retained. Keys resembling credentials, cookies, passwords, secrets,
  tokens, authorization, or API keys are removed recursively.
- Nonzero exit: `search_failed`; malformed or identity-mismatched JSON:
  `provider_protocol_error`; missing executable: `adapter_unavailable`.
- Stderr diagnostics are redacted and bounded to 320 characters. Providers must still avoid writing
  secrets because process owners can inspect local logs independently of TraceFetch.

Use `tracefetch schema provider-manifest`, `provider-request`, and `provider-response` for the exact
draft 2020-12 JSON schemas.

## Internal provider boundary

An internal provider should emit opaque locators instead of reusable document tokens or private
URLs. TraceFetch prevents cross-scope aggregation for isolated providers, but it cannot decide what
content an enterprise CLI is authorized to return. The provider remains responsible for access
control, redaction, and retention policy.
