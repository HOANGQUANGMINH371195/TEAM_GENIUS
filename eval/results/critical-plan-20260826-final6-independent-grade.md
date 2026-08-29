# Independent grade — final6 staging run (2026-08-26)

## Verdict

**FAIL — DO NOT PROMOTE.** The deterministic harness reports `7/7 PASS`, but
strict independent grading gives only `1/7` full content pass. Case 01 now has
an answer, but the legally material threshold wording is incomplete and the
route is extremely slow. Five cases contain a core rule or safe abstention but
fail completeness, scope, currentness, or citation quality; case 07 is safe but
not useful as an answer.

## Reproducibility

- Artifact: `eval/results/critical-plan-20260826-final6`
- Artifact SHA-256: `9243e85630bb667a43a18fd8facb322c7b76cd7ebc7129f889294d4a838decbf`
- Fixture: `eval/cases/critical-bhyt-7.jsonl`
- Fixture SHA-256: `461329a9452bd38104f3c78f6e45b53fe0217250d5cca9cbf0f330ddfb2a9ebb`
- Model: `gpt-5.6-luna`

The grade uses public document numbers and answer content. Internal IDs were
not used as pass criteria.

## Case-level results

| Case | Legal result | Direct citation precision | Temporal | Verdict |
|---|---|---:|---|---|
| 01 — 5-year co-pay | Current core rule; threshold transition gap | 2/10 | Partial | Partial fail |
| 02 — provincial self-referral | Core rates; scope gap/duplicate bullet | 2/9 | Partial | Partial fail |
| 03 — student 2026 | Basis only; amount/support missing | 1/10 | Partial | Partial fail |
| 04 — referral validity | Safe abstention; old-law currentness risk | 3/10 | Risk | Partial only |
| 05 — emergency/no referral | Core emergency rule; user scope gap | 1/9 | Pass for stated rule | Partial fail |
| 06 — cosmetic service | Correct | 1/1 | Pass observed | Pass content only |
| 07 — historical 2005 | Safe but generic abstention | 0/1 | Negative only | Partial only |

Case 01 is no longer an availability failure, but the answer says “six times
the reference level” while the fixture also requires the older “more than six
months of base salary” wording. The response does not identify whether this is
a historical/current transition. Its citations are all provenance-unverified.

Cases 02 and 05 quote the principal 51/2024/QH15 rules, but return raw legal
bullets without fully resolving the user's facility/absence-of-referral facts.
Case 03 safely avoids inventing money but does not supply the amount or support
rate. Case 04 is a fixture-permitted safe abstention because 01/2025/TT-BYT was
not found in the checked raw metadata, but its 2008 source does not prove
currentness. Case 06 is correct. Case 07 avoids a false historical-current
claim but gives no current authority.

## Citation/quote/span/hash audit

There are 50 citations. All have a quote, source span and `text_sha256`, and all
50 exactly link to a corresponding `retrieved_evidence` record by document,
span and hash. This is a material trace improvement.

It is not yet proof of canonical evidence:

- only 21/50 citations have `provenance_verified=true`;
- 29/50 explicitly report `provenance_verified=false`;
- only 17/50 have a hash equal to the SHA-256 of the emitted quote;
- direct core citations in cases 01, 02, 03 and 05 are provenance-unverified;
- many provenance-true citations are unrelated technical, administrative,
  local, military, or old-law passages.

Strict manual direct-support precision is approximately `10/50 = 20%`.
Runtime `verified_claim_count` therefore cannot be accepted as an independent
legal-grounding score.

## Trace and phase audit

All seven cases expose a trace ID, retrieval phase timings and a non-empty
evidence inventory. Citation-to-evidence linkage is complete. Retrieval phases
include release recall, embedding, Qdrant, lexical fusion and, on six cases,
hydration and rerank selection.

Trace completeness remains **PARTIAL / NOT OBSERVABLE at PLAN level**:

- verifier decisions are aggregate metadata rather than selected claim-to-span
  decisions;
- six successful cases report generation `skipped`; only case 04 reports
  provider token usage;
- no cost, TTFT/first-useful-SSE, repeated cold/warm/concurrency, or graph-path
  evidence is recorded;
- `relation_count` is zero across this artifact.

The new trace proves the evidence fallback and phase timing are being emitted,
but not the complete PLAN contract of tokens, claims, citations, confidence,
cost, failure reason and browser TTFT for every request.

## Latency versus PLAN.md

The artifact reports p50 `15,496.58 ms` and p95 `25,556.06 ms`. Both values are
reproducible from the seven per-case latencies using the conventional median and
linear-interpolation p95. The sample is one run only, so repeated-run p95 is
still required.

The release gates nevertheless fail: thematic cases take `14.02–27.92 s`,
temporal cases take `15.18–20.03 s`, and no first-useful-SSE timing exists. This
exceeds the PLAN topical p95 ≤8 s and temporal p95 ≤15 s budgets.

## Security

No returned answer exposed an internal ID, chunk ID, dataset ID, or secret:
`PASS_OBSERVED` for answer text. This does not prove hidden trace/log/provider
redaction, and this seven-case fixture is not a complete injection suite.

## Required gates before promotion

1. Resolve case 01's current/legacy threshold semantics and preserve both facts
   with effective-date evidence.
2. Make accepted-authority citations provenance-valid and remove unrelated
   topical citations.
3. Keep the evidence inventory, but expose canonical source/release/hash
   semantics and claim-to-span mappings that an independent grader can verify.
4. Add the case 03 numeric amount/support-rate path and explicit no-referral
   scope for case 05; provide a current authority or a useful currentness
   explanation for case 07.
5. Emit verifier, generation/provider, usage, cost and TTFT traces for every
   request, including skipped/degraded routes.
6. Reduce release-recall, lexical and hydration bottlenecks; repeat cold/warm/
   concurrency runs against route budgets.
7. Retain human legal adjudication before any release accuracy claim.
