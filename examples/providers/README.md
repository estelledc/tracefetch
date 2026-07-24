# Example command provider

`demo-provider.py` is a deterministic protocol example, not an internet search backend.

```bash
tracefetch doctor --providers examples/providers/providers.json
tracefetch search "agent evidence" \
  --scope demo \
  --providers examples/providers/providers.json
```

The manifest resolves `./demo-provider.py` relative to its own directory. The process reads one
provider request from stdin and returns one provider response on stdout.
