# Feature usefulness audit

This is a code-path audit, not a claim that a feature is legally correct. Run
`scripts/diagnose_queries.py` against real questions before changing ranking.

## Current product paths

| Feature | Real backend path | What it currently proves | Main limitation observed |
|---|---|---|---|
| Chat | `/api/v1/chat`, `/api/v1/chat/stream` → LangGraph | Route, retrieval, verification, synthesis and public citations | A bad authority candidate set can still produce a safe but useless “not found” answer |
| Scenario comparison | `/api/v1/calculator/bhyt/draft` → `/api/v1/calculator/bhyt/scenarios` | Extracts source-stated values, then performs bounded Decimal arithmetic | Draft suggests values but does not infer legal applicability or automatically turn a chat question into scenarios |
| Legal timeline | `/api/v1/legal/timeline` | Hydrates public document metadata and bounded Neo4j relationships | Requires a document number; no question-to-document hand-off |
| Eligibility checklist | `/api/v1/eligibility/checklist` | Requests only missing facts and persists them owner/conversation-scoped | Checklist does not itself produce the final legal analysis; user must return to chat |

## Smoke evidence (real provider)

The two-query diagnostic run is stored locally in `eval/results/` (ignored from
git). The important finding was:

- greeting fast path: ~4.6s, no retrieval call;
- five-year BHYT question: ~23.2s total;
  - release recall: ~10.8s and returned 0 candidates;
  - lexical: ~9.7s and returned 24 candidates;
  - final evidence: 4 lexical passages from unrelated accounting/support
    documents, with no verified authority metadata;
  - generation: ~3.7s and correctly failed closed with no-answer.

This is a retrieval/authority-selection failure, not a model hallucination. The
new diagnostic script records this distinction per query, including route,
stage timings, hashed lineage keys, authority/provenance flags, final evidence,
citations and rendered response.

## How to run

```bash
UV_CACHE_DIR=.cache/uv uv run --python 3.11 \
  --with-requirements requirements/dev.lock \
  python scripts/diagnose_queries.py \
  --query "Mức đóng BHYT hiện nay và mức hỗ trợ theo từng nhóm là bao nhiêu?" \
  --query "Thủ tục chuyển tuyến khám chữa bệnh BHYT gồm những gì?"
```

The output is JSONL so latency regressions and wrong-layer failures can be
compared without exposing storage IDs to browser users.
