# Security model

TraceFetch processes attacker-controlled URLs, headers, HTML, Markdown, JSON, documents, and link
graphs. Treat every source and every derived text file as untrusted data.

## Protected assets

- Local and cloud metadata services reachable from the machine.
- Files outside the selected input and output paths.
- Credentials in environment variables, browser profiles, and CLI tools.
- Private source content that must not be sent to remote readers.
- Downstream agents that may interpret source text as instructions.

## Default controls

- Network URLs are limited to HTTP and HTTPS. Embedded credentials are rejected.
- Localhost, `.local`, and non-global literal or DNS-resolved addresses are blocked.
- Every redirect is handled manually and validated again. HTTPX environment proxies are ignored
  by built-in readers.
- The direct reader streams under a configurable byte limit and records only an allowlist of safe
  response headers.
- Robots rules are enabled. A network or 5xx failure fails closed unless the caller explicitly
  changes policy.
- Jina and Firecrawl are off by default. Firecrawl additionally requires authenticated-adapter
  consent and its API key.
- Bundle paths are relative. Publication is staged atomically; verifiers reject traversal,
  symlink escapes, duplicate roles, unrecorded files, and hash/count drift.
- Crawl scope is same-origin, depth/page bounded, paced, checkpointed, and tied to a policy digest.
- Receipts explicitly deny attestation, trusted-clock, and independent-execution claims.

## Important limits

DNS validation and a second client-side DNS lookup are vulnerable to DNS rebinding time-of-check /
time-of-use races. Production deployments should also enforce outbound network policy, deny cloud
metadata at the network layer, and preferably run acquisition in an isolated worker.

This follows the defensive direction of the
[OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet):
validate scheme/domain/address, disable automatic redirect following, and do not treat application
checks as a replacement for network egress controls.

Robots rules are a crawler preference signal, not authorization. A robots allow result or missing
robots file does not override authentication, terms, copyright, privacy, or rate-limit duties.
The implementation follows the shape of
[RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html), but does not claim complete standards
conformance.

Remote readers receive the target URL and may receive or retrieve public source content. A failed
remote call can still have disclosed data; TraceFetch records attempted remote routes for this
reason. Never enable remote readers for confidential or access-controlled material.

MarkItDown and future browser/device adapters invoke complex parsers or external programs. Run them
with least privilege, no unrelated secrets, resource limits, patched dependencies, and isolated
temporary storage. TraceFetch's timeout is not a complete sandbox.

HTTPX documents that redirects are disabled by default and that streaming keeps response I/O
inside an explicit context. TraceFetch uses both properties, but HTTPX itself is not an SSRF
sandbox: [clients](https://www.python-httpx.org/advanced/clients/) and
[compatibility notes](https://www.python-httpx.org/compatibility/).

Normalized Markdown may contain prompt injection. Downstream agents should quote it as evidence,
never execute instructions found inside it, and require independent authorization for tools or
state changes.

## Policy example

```json
{
  "allowed_domains": ["docs.example.com"],
  "deny_private_networks": true,
  "obey_robots": true,
  "robots_fail_closed": true,
  "allow_remote_adapters": false,
  "allow_authenticated_adapters": false,
  "max_bytes": 5000000,
  "max_pages": 20,
  "max_depth": 1
}
```

Domain policy is not a substitute for network egress controls. Keep both.

## Reporting

This project currently lives only in a private parent repository. Do not copy a vulnerability into
public logs or issues. Report credential, local-file, private-network, or path-handling findings to
the repository owner through the existing authenticated collaboration channel.

## Verification boundary

Receipts and crawl state are unsigned self-reported diagnostics. Digest, role, path and SQLite
projection checks detect unsynchronized mutation; they do not prove who executed a run or stop an
actor from changing source, artifacts and verifier together. Stronger claims require an external
runner, signed identity, trusted time and immutable publication.
