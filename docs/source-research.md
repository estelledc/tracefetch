# Source research and adoption matrix

Research snapshot: 2026-07-24. Repository existence, archive state, descriptions and license
fields were rechecked through the GitHub API. Security and runtime decisions use official RFC,
OWASP, HTTPX and SQLite documentation.

## Premise correction

No tool can defensibly promise clean data from every website. Authentication, robots policy,
terms, network topology, rendering, anti-automation controls, changing DOMs, data licenses and
source quality all create hard boundaries.

TraceFetch therefore does not combine ten scraping engines into one stealth crawler. It absorbs
the reusable engineering strengths, exposes expensive capabilities as adapters, and explicitly
rejects bypass-oriented defaults.

## The ten projects

| Source | Verified strength | TraceFetch decision | v0.1 evidence |
|---|---|---|---|
| [Firecrawl](https://github.com/firecrawl/firecrawl) (AGPL-3.0) | Search/scrape/crawl API, rendered pages, clean Markdown | Optional authenticated remote reader; no source copied or embedded | `FirecrawlReader`, explicit remote and authenticated flags, route disclosure |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) (Apache-2.0) | LLM-oriented Markdown, citations, structured extraction, sessions, cache, crash recovery | Adopt evidence addressing and recovery principles; reserve a local rendered-reader adapter | Line anchors and hashes, raw/derived split, SQLite resume, `doctor` detection |
| [browser-use](https://github.com/browser-use/browser-use) (MIT) | Real browser control, dynamic pages, reusable authenticated profiles | Browser adapter boundary only; login and state-changing actions are out of scope | `ReaderAdapter` injection contract; no bundled browser automation |
| [Crawlee](https://github.com/apify/crawlee) (Apache-2.0) | Persistent request queues, retries, throttling, sessions, scalable crawling | Adopt bounded queue/retry/resume semantics without importing a Node runtime | SQLite queue, same-origin crawl, persisted reader/policy digest, rate interval |
| [Scrapy](https://github.com/scrapy/scrapy) (BSD-3-Clause) | Mature staged crawling, middleware, item pipelines, extension discipline | Adopt strict stage separation and typed contracts | Search, policy, reader, normalize, bundle, verify modules and Pydantic schemas |
| [MarkItDown](https://github.com/microsoft/markitdown) (MIT) | PDF, Office, HTML, image, and audio conversion to Markdown | Optional local subprocess normalizer; never required for core install | `tracefetch[markitdown]`, temp-file isolation, timeout, stable failure record |
| [Scrapling](https://github.com/D4Vinci/Scrapling) (BSD-3-Clause) | Adaptive selectors, concurrency, throttling, cache, pause/resume, robots support | Adopt resilience and pacing ideas; reject stealth/anti-bot bypass as a default | Crawl checkpoints and throttle; no fingerprint spoofing or CAPTCHA bypass |
| [scrcpy](https://github.com/Genymobile/scrcpy) (Apache-2.0) | Observable Android display/control for app-only surfaces | Device-capture extension target, not a web reader and not provenance by itself | `doctor` reports availability; no bundled device control |
| [AutoScraper](https://github.com/alirezamika/autoscraper) (MIT) | Example-driven extraction rules instead of hand-written selectors | Candidate for versioned extraction recipes after evidence contracts stabilize | Not implemented in v0.1; no adaptive-rule claim |
| [curl-impersonate](https://github.com/lwthiker/curl-impersonate) (MIT) | Browser-like TLS and HTTP/2 fingerprints | Explicitly rejected as a default acquisition strategy | Direct reader identifies itself as TraceFetch; no impersonation path |

“Not implemented” is intentional evidence, not a missing footnote. Browser/device control and
learned extraction require distinct authorization, privacy, reproducibility, and test contracts.

## Additional design source

[Agent Reach](https://github.com/Panniantong/Agent-Reach) (MIT) contributes a particularly useful
control-plane pattern: route to an already capable backend, expose health through `doctor`, and
degrade explicitly instead of pretending every platform shares one transport. TraceFetch applies
that pattern to Exa, GitHub, Jina, Firecrawl, MarkItDown, and future local adapters.

## Comparable projects and boundary

- [Octopus Scout](https://github.com/octoryn/octopus-scout) focuses on governed AI-native web/PDF
  ingestion. TraceFetch is a smaller local CLI and adapter/evidence contract.
- [IngestForge](https://github.com/Parvaz-Jamei/IngestForge) extends safe ingestion into AI
  generation, provenance ledgers, RAG records, and exports. TraceFetch intentionally stops before
  generation and indexing so other projects can choose those layers.
- [Episteme](https://github.com/Ismail-elkorchi/episteme) focuses on source snapshots, structured
  extraction, hashes, metadata, and diffing. TraceFetch adds live discovery routing, network policy,
  bounded crawl state, and remote-adapter disclosure.

The meaningful differentiator is not “more scraping.” It is a vendor-neutral boundary between
discovery/acquisition and downstream use, with portable artifacts that can be verified without
importing TraceFetch.

## Security foundations

- [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) defines the Robots Exclusion Protocol.
  It also makes clear that robots rules are not access authorization and defines conservative
  behavior for server/network unavailability.
- The [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
  motivates allowlists, strict input validation, redirect control, and private-address blocking.

## Runtime foundations

- [HTTPX clients and streaming](https://www.python-httpx.org/advanced/clients/) support explicit
  client lifetime and bounded streaming reads; the
  [compatibility notes](https://www.python-httpx.org/compatibility/) document the no-auto-redirect
  default used by the direct reader.
- [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html) and the
  [transactional guarantee](https://sqlite.org/transactional.html) motivate committed local queue
  transitions and crash recovery. They do not turn one SQLite file into a distributed scheduler.

## Code and license boundary

TraceFetch is original Apache-2.0 code. It uses documented interfaces and design ideas; it does not
copy implementation source from the compared projects. Firecrawl is contacted only as an optional
external service. Optional dependencies retain their own licenses and must be reviewed by the
integrator. This matrix is engineering provenance, not legal advice.
