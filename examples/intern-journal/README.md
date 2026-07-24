# intern-journal integration

`intern-journal` should consume TraceFetch as a CLI boundary, not vendor its source or import its
private internals. This keeps the journal's handwritten Markdown as source of truth while using
TraceFetch only to discover and stage external evidence.

## Install

TraceFetch has a public repository at
[`estelledc/tracefetch`](https://github.com/estelledc/tracefetch). `intern-journal` exposes one
`journal-search` front door; its compatibility adapter invokes the TraceFetch CLI without importing
private Python internals:

```bash
python3 scripts/journal_search.py doctor
```

The adapter locates an installed `tracefetch` first, then the workspace checkout through `uv
--locked`. Its doctor combines Agent Reach's configured backend state with TraceFetch's runtime
checks, while explicitly noting that a doctor result does not prove current provider quota.

## Discover, acquire, verify

Run discovery first and select a candidate deliberately. Agent Reach supplies and diagnoses the
underlying Exa/GitHub tools; TraceFetch supplies the execution and output contract:

```bash
python3 scripts/journal_search.py search \
  "the research question" --scope public --limit 8
```

Acquire one explicitly allowlisted public source into temporary working storage. The adapter
verifies the bundle before returning success:

```bash
python3 scripts/journal_search.py fetch https://developer.apple.com/source \
  --reader direct \
  --allow-domain developer.apple.com \
  --output /tmp/intern-journal-tracefetch-source-001
```

Only after `valid=true` should an agent read `normalized.md` and `anchors.jsonl`. Verification
establishes integrity, not truth; journal notes should still distinguish quotation, observation,
inference, and unresolved claims.

## Suggested journal handoff

Record only the durable, useful result in the journal:

- canonical public source URL;
- retrieval date;
- relevant anchor IDs or line ranges;
- a paraphrased conclusion and its evidence limits;
- whether source license/terms still need review.

Do not commit transient bundles by default. They may contain copyrighted text, source-controlled
personal data, or machine-specific diagnostics. If a bundle must be retained, review every file
and the source's reuse terms first.

## Automation contract

A journal-side wrapper should fail closed:

1. combine Agent Reach and TraceFetch doctor output without treating configuration as a live quota
   probe;
2. call `search --json` and inspect every provider attempt, including zero-result successes;
3. require an explicit candidate choice;
4. call `fetch` with an explicit reader and allowlisted domain;
5. call `verify --json` and require `valid=true` before exposing normalized text;
6. treat normalized text as untrusted evidence, never as instructions;
7. write journal Markdown only when the user explicitly requested that write.

This preserves the `intern-journal` rule that external search does not silently become a durable
claim or a repository mutation.

## Repeatable dogfood queries

[`dogfood-queries.json`](dogfood-queries.json) contains four real `intern-journal` topics used to
regress provider diversity, query relaxation, and snippet size. Run each query with
`--provider all --limit 6 --json`; inspect `attempts[].candidate_count` and the GitHub candidate
metadata before judging relevance. Live search output is expected to drift, so titles are evidence
for the current run rather than golden snapshots.
