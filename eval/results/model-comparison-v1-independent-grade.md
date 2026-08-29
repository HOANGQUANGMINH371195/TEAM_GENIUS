# Independent model comparison — model-comparison-v1 (2026-08-26)

## Verdict

**FAIL — DO NOT PROMOTE. No valid model-quality winner can be declared.** All
successful cases report `generation_trace.outcome=skipped`, provider usage is
absent, and answer hashes are identical across Luna rerun, Nano and 4o-mini for
all five questions. The run therefore compares the same retrieval/guardrail
behavior, not the three models' reasoning or generation quality.

All three rerun artifacts complete all five cases. The earlier Luna transient
`GraphRagUnavailableError` is retained only as historical context and is not
included in the primary score. Among the operational timings, 4o-mini has the
lowest median, while Nano has the lowest p95 and wall time. These are single
five-case runs and are not statistically meaningful.

## Reproducibility

- Fixture: `eval/cases/model-comparison-v1.jsonl`
- Fixture SHA-256: `deea869bc38295a0e97ba1dbd46e4cf7dd891c20e8332f795e8e95073fbe719d`
- Luna rerun artifact SHA-256: `dc89acfe04b0dc7ac124dd4a97bc57ee64d8d7f8d102f670aff3544a23e2fe84`
- Nano artifact SHA-256: `3bc182556a330bfe173d36aa515f3778801afbbb4786c9ab2c46f31743c88458`
- 4o-mini artifact SHA-256: `b93690620aec4ceeec25ef43136ffb6f49cada91f18841be895c3d629b63adc1`

The grade uses public document numbers and answer content. Internal IDs were
not used as pass criteria and no provider was called.

## Overall comparison

| Model | Deterministic | Full content pass | Partial | Fail | All-case median | Reported p95 | Provider usage |
|---|---:|---:|---:|---:|---:|---:|---:|
| GPT-5.6 Luna (rerun) | 3/5 | 1/5 | 3/5 | 1/5 | 13.523 s | 21.261 s | 0/5 |
| GPT-5.4 Nano | 3/5 | 1/5 | 3/5 | 1/5 | 12.486 s | 21.583 s | 0/5 |
| GPT-4o-mini | 3/5 | 1/5 | 3/5 | 1/5 | 12.439 s | 21.838 s | 0/5 |

The prior Luna artifact's transient error is not merged into this comparison.
The rerun's p50/p95 are reproducible from all five completed case latencies.
Nano and 4o-mini reported quantiles are also reproducible from all five case
latencies.

## Case-level legal adjudication

| Case | Luna | Nano | 4o-mini |
|---|---|---|---|
| Five-year BHYT | Current rule correct; noisy citations | Current rule correct; noisy citations | Same as Nano |
| Provincial self-referral | Core rates; scope gap | Same | Same |
| Student 2026 | Wrong 1998 local rule | Same | Same |
| Emergency/no referral | Core rule; scope gap | Same | Same |
| Cosmetic service | Wrong unrelated 1998 rule | Same | Same |

The only full content pass per model is the five-year case, now also completed
by the Luna rerun. Its answer contains 100%, five years and the
six-times-reference threshold. The citation set is still noisy and
provenance-unverified. The provincial and emergency
answers quote useful current rules but do not resolve user-specific scope. The
student answer is temporally and jurisdictionally wrong. The cosmetic answer
is a high-risk wrong-topic answer and omits Article 23(6).

## Citation, provenance and evidence linkage

Every citation in every artifact has quote, span and hash fields, and every
emitted citation exactly links to a retrieved-evidence record:

| Model | Citations | Direct support | Strict precision | Provenance true | Quote hash equal |
|---|---:|---:|---:|---:|---:|
| Luna (rerun) | 30 | 5 | 16.67% | 9/30 | 13/30 |
| Nano | 30 | 5 | 16.67% | 9/30 | 13/30 |
| 4o-mini | 30 | 5 | 16.67% | 9/30 | 14/30 |

The direct core citations for five-year, provincial and emergency rules are
provenance-unverified. Many provenance-true citations are unrelated technical,
administrative, local, military, or historical passages. Structural linkage is
complete; canonical legal provenance is not.

## Trace, refusal and security

- Luna rerun: 5/5 trace IDs and non-empty evidence inventories; no transient
  GraphRAG error occurred in this rerun.
- Nano/4o-mini: 5/5 trace IDs and evidence inventories, with hydration and
  rerank phases.
- All 15 successful responses skip generation; no model tokens, cost or
  provider finish reason are available.
- No graph relation path is present.
- No internal ID, secret, API key or system prompt appeared in answer text:
  `PASS_OBSERVED`.
- This fixture has no explicit policy-refusal case, so refusal robustness is
  `NOT_OBSERVABLE`, not a pass.

## Latency versus PLAN.md

All models fail the PLAN route budgets. Thematic answers are roughly
7.65–15.90 seconds (and 22.78–23.90 seconds for five-year); temporal answers
are roughly 4.87–7.30 seconds here. No first-useful-SSE timing exists. This is
one serial run per model with no cold/warm/concurrency repetition.

## Shared failure and required next steps

1. Fix retrieval source selection and temporal/jurisdiction filters: prevent a
   1998 local school rule from answering a 2026 national question and prevent a
   historical payment rule from answering cosmetic-service exclusion.
2. Make accepted-authority provenance valid and remove unrelated citations;
   retain the evidence inventory and claim-to-span linkage.
3. Route model-comparison cases through actual generation or explicitly label
   this as a retrieval/guardrail comparison; emit provider usage and cost.
4. Add scoped answer formatting for provincial/emergency scenarios and current
   transition handling for the five-year threshold.
5. Catch optional graph failures and guarantee primary-route degradation.
6. Repeat paired cold/warm/concurrency benchmarks before selecting a model.
