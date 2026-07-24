# Migrating from 0.1 to 1.0

TraceFetch 1.0 keeps every 0.1 fetch, ingest, crawl, verify, receipt, and Python search-provider API.
The deliberate change is the CLI search default: TraceFetch is now a local-first unified search
product rather than a public-provider-only command.

## Search CLI mapping

| 0.1 command | 1.0 equivalent | Output |
|---|---|---|
| `tracefetch search QUERY` | `tracefetch search QUERY --scope public --provider auto` | `tracefetch.search-results.v1` |
| `tracefetch search QUERY --provider all --json` | unchanged compatibility mode | `tracefetch.search.v1` |
| none | `tracefetch search QUERY` | local workspace, `tracefetch.search-results.v1` |
| none | `tracefetch search QUERY --scope auto --provider all` | local + public |

An explicit `--provider` without `--scope` selects the compatibility path for the 1.x line. Add
`--scope public` when migrating to the new envelope.

## Output changes

All CLI commands now emit JSON by default. Use `--format pretty` only for interactive debugging.
Errors always use `tracefetch.error.v1` on stderr, including malformed arguments. The legacy
`--json` flag remains accepted as a no-op compatibility alias.

The new search candidate uses `locator` rather than requiring a URL. It also adds:

- `scope` and `provider`;
- `source_class` and `evidence_state`;
- `sensitivity`;
- backend attempt records and warnings.

Local locators are repository-relative `path:line` strings. Public locators are URLs. Command
providers define opaque locators appropriate to their trust domain.

## External systems

0.1 Python callers can continue injecting a `SearchProvider` into `search_sources`. CLI consumers
that need papers, social platforms, or internal sources should migrate to an explicit
`tracefetch.providers.v1` manifest and provider process. TraceFetch does not import arbitrary Python
modules or auto-run executables.

## Version support

The `.v1` evidence and crawl schemas remain compatible. `tracefetch.search.v1` is retained for the
1.x compatibility window. New integrations should target `tracefetch.search-results.v1`; removal
of the compatibility path would require TraceFetch 2.0.
