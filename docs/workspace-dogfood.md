# Workspace dogfood report

Date: 2026-07-24. This report records one bounded self-improvement loop: run TraceFetch against
four real software-research topics, inspect the output, change only reproduced failure modes, then
run the same queries again.

## Query suite

The machine-readable source is
[`examples/workspace/dogfood-queries.json`](../examples/workspace/dogfood-queries.json). It covers
source provenance, UIKit lifecycle, Swift concurrency, and browser/device evidence.

All public runs used `provider=all` and a result limit of six. Exa and GitHub both completed
successfully.

## Baseline observations

| Query | Exa | GitHub visible candidates | Useful signal |
|---|---:|---:|---|
| web crawler provenance | 4+ | 0 | Exa found provenance-oriented crawlers |
| UIViewController lifecycle viewWillAppear refresh | 6 | 0 | Exa ranked Apple documentation first |
| Swift concurrency MainActor Sendable actor isolation | 6 | 0 | Exa found Apple, Swift Evolution, and Swift Forums |
| AI browser device automation evidence protocol | 6 | 0 | Exa found browser evidence projects, with several self-reported claims |

GitHub repository search treated each long natural-language query as an overly strict conjunction.
Because `SearchAttempt` did not report candidate counts, agents had to infer the zero-result
condition from merged output. Exa highlights also contained standalone `...` separators and could
consume 2,000 characters per candidate.

## First iteration

TraceFetch now retries GitHub at most once and only after an exact zero-result response. The
fallback uses two non-generic topic terms, sorts the broader query by stars, and records its query
in candidate metadata. `SearchAttempt.candidate_count` makes successful emptiness explicit. Exa
snippets remove separator-only lines and are bounded to 1,200 characters.

The same four queries then produced six Exa and six GitHub provider candidates before merge. The
six visible results alternated providers. Actual fallback variants included `web crawler`,
`UIViewController lifecycle`, `Swift concurrency`, and initially `AI browser`.

## Second iteration

The first fallback fixed recall but `AI browser` was too broad. The query planner now treats `AI`,
`device`, and `protocol` as generic inside a longer query, yielding `browser automation`.

The live top six GitHub results after that change were:

1. `vercel-labs/agent-browser`
2. `SeleniumHQ/selenium`
3. `apify/crawlee`
4. `segment-boneyard/nightmare`
5. `hangwin/mcp-chrome`
6. `pinchtab/pinchtab`

Silent snippet truncation was also corrected: truncated Exa text now ends with `[truncated]`, and
candidate metadata reports `snippet_truncated=true`.

## Consumer iteration

A four-query run through a consumer adapter reproduced an Exa HTTP 429 while GitHub still returned
six candidates. The raw tool stack previously filled `attempts[].message`; provider failures are
now bounded and the 429 path is classified as `rate_limited`.

A subsequent official Swift.org acquisition returned HTTP 200 and a valid evidence bundle, but
inspection found XML processing-instruction text inside permalink icons polluting headings. HTML
normalization now removes that noise; the same bytes re-normalized to clean `Overview`, `Articles`,
and `Contributing` anchors and again passed bundle verification.

## 1.0 product extraction

The original consumer kept local ranking, sensitivity routing, and external-tool normalization in
project code. TraceFetch 1.0 extracts the reusable parts into the independent product:

- local workspace search with relative locators and a Python fallback;
- `local`, `public`, and explicit provider scopes under `tracefetch.search-results.v1`;
- fixed-argv command providers for paper, social, account-visible, or internal backends;
- machine-readable defaults, sensitive-provider opt-in, and internal isolation.

Consumers now need only project-specific provider wrappers and policy decisions. They install an
immutable TraceFetch release rather than vendoring its source tree.

## What this proves

- Both configured public providers executed for the four-query suite.
- A reproduced GitHub zero-recall failure is observable and boundedly recoverable.
- Merged results have provider diversity for these queries.
- Search output discloses query relaxation, truncation, sensitivity, and backend attempts.
- One official public source produced an internally valid evidence bundle.

## What this does not prove

- Four queries do not establish general search quality or production reliability.
- Stars are a coarse prior, not correctness or relevance evidence.
- Search descriptions contain unverified third-party claims.
- No Recall@K, NDCG, latency distribution, production SLA, or user task-success study exists yet.
- Doctor output proves configuration/executable state, not current quota or end-to-end availability.
