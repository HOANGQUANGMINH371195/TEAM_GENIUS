# Go migration workspace

This directory is the Phase 1 compatibility workspace described in
[`PLAN-GO-REFACTOR.md`](../PLAN-GO-REFACTOR.md). It is intentionally read-only
and does not receive production traffic yet.

## Compatibility probe

The standard-library-only probe measures concurrent `/health` and `/ready`
round trips without importing database or LLM SDKs:

```bash
go run ./cmd/compat-probe -url http://localhost:8000
P151_API_URL=https://api.example.com go run ./cmd/compat-probe
```

Exit status is non-zero if either endpoint is not a 2xx response. Keep this
probe as a transport baseline; database adapters and answer parity belong to
the next phases and must be gated by the locked benchmark before promotion.
