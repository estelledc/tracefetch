# intern-journal dogfood report

Date: 2026-07-24. This report records one bounded self-improvement loop: use TraceFetch on four
real `intern-journal` research topics, inspect the output, change only reproduced failure modes,
then run the same queries again.

## Query suite

The machine-readable source is
[`examples/intern-journal/dogfood-queries.json`](../examples/intern-journal/dogfood-queries.json).
It covers source provenance, UIKit lifecycle, Swift concurrency, and browser/device evidence.

All runs used `provider=all` and a result limit of six. Exa and GitHub both completed successfully.

## Baseline observations

| Query | Exa | GitHub visible candidates | Useful signal |
|---|---:|---:|---|
| web crawler provenance | 4+ | 0 | Exa found provenance-oriented crawlers |
| UIViewController lifecycle viewWillAppear refresh | 6 | 0 | Exa ranked Apple documentation first |
| Swift concurrency MainActor Sendable actor isolation | 6 | 0 | Exa found Apple, Swift Evolution, and Swift Forums |
| AI browser device automation evidence protocol | 6 | 0 | Exa found browser evidence projects, with several self-reported claims |

The GitHub adapter itself succeeded, but GitHub repository search treated each long natural-language
query as an overly strict conjunction. Because `SearchAttempt` did not report candidate counts,
agents had to infer the zero-result condition from the merged output. Exa highlights also contained
many standalone `...` separators and could consume 2,000 characters per candidate.

## First iteration

TraceFetch now retries GitHub at most once, and only after an exact zero-result response. The
fallback uses two non-generic topic terms, sorts the broader query by stars, and records its query
in candidate metadata. `SearchAttempt.candidate_count` makes successful emptiness explicit. Exa
snippets remove separator-only lines and are bounded to 1,200 characters.

The same four queries then produced six Exa and six GitHub provider candidates before merge. The
six visible results alternated providers. Examples of actual fallback variants were `web crawler`,
`UIViewController lifecycle`, `Swift concurrency`, and initially `AI browser`.

## Second iteration

The first fallback fixed recall but `AI browser` was too broad: visible GitHub results included
weakly related repositories. The query planner now treats `AI`, `device`, and `protocol` as generic
inside a longer query, yielding `browser automation`.

The live top six GitHub results after that change were:

1. `vercel-labs/agent-browser`
2. `SeleniumHQ/selenium`
3. `apify/crawlee`
4. `segment-boneyard/nightmare`
5. `hangwin/mcp-chrome`
6. `pinchtab/pinchtab`

Silent snippet truncation was also corrected: truncated Exa text now ends with `[truncated]`, and
candidate metadata reports `snippet_truncated=true`.

## What this proves

- Both configured providers execute from the `intern-journal` root.
- A reproduced GitHub zero-recall failure is now observable and boundedly recoverable.
- The merged result set has provider diversity for these four queries.
- Search output consumes less context and discloses relaxation and truncation.

## What this does not prove

- These four queries do not establish general search quality or production reliability.
- Stars are a coarse quality prior, not correctness or relevance evidence.
- Exa and repository descriptions contain unverified third-party claims.
- No baseline-vs-TraceFetch relevance labels, Recall@K, NDCG, latency distribution, or user task
  success study exists yet.
- Agent Reach and TraceFetch doctor output proves configuration/executable state, not current
  provider quota or end-to-end availability.

## Consumer integration iteration

The initial report stopped at a manually callable tool. `intern-journal` now has a project-side
adapter used by `research-gap`: Agent Reach diagnoses the Exa/GitHub backends, TraceFetch executes
the structured search and evidence flow, and the journal retains its own budget, allowlist, and
knowledge-state rules. The legacy WebSearch/WebFetch path is now an explicit unbundled fallback,
not a silent equivalent.

A live four-query run through that adapter reproduced an Exa HTTP 429 while GitHub still returned
six candidates. The raw mcporter stack previously filled `attempts[].message`; provider failures
are now bounded and the 429 path is classified as `rate_limited`. A subsequent official Swift.org
acquisition returned HTTP 200 and a valid evidence bundle, but inspection found XML processing
instruction text inside permalink icons polluting headings. HTML normalization now removes that
noise; the same bytes re-normalized to clean `Overview`, `Articles`, and `Contributing` anchors and
again passed bundle verification.
