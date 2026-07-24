# intern-journal integration

`intern-journal` should consume TraceFetch as a CLI boundary, not vendor its source or import its
private internals. This keeps the journal's handwritten Markdown as source of truth while using
TraceFetch only to discover and stage external evidence.

## Install

TraceFetch currently lives inside the private `intern-journal` workspace and has no separate
remote repository. From the parent repository, run the local working tree explicitly:

```bash
uv sync --project explorations/own/tracefetch --extra dev --python 3.11
uv --project explorations/own/tracefetch run tracefetch doctor --json
```

The examples below abbreviate that prefix as `tracefetch`; no public package or clone command is
currently available.

## Discover, acquire, verify

Run discovery first and select a candidate deliberately:

```bash
tracefetch search "the research question" --provider all --limit 8 --json \
  > /tmp/tracefetch-candidates.json
```

Acquire one public source into ignored working storage:

```bash
tracefetch fetch https://example.com/source \
  --reader direct \
  --output .cache/tracefetch/source-001 \
  --json

tracefetch verify .cache/tracefetch/source-001 --json
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

1. call `search --json`;
2. require an explicit candidate choice;
3. call `fetch` with an explicit reader and, when needed, a reviewed policy file;
4. call `verify --json` and require `valid=true`;
5. expose normalized text as untrusted evidence, never as instructions;
6. write journal Markdown only when the user explicitly requested that write.

This preserves the `intern-journal` rule that external search does not silently become a durable
claim or a repository mutation.
