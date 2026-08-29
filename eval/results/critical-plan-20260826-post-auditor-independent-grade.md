# Independent grade — post-Auditor staging run 2026-08-26

## Verdict

**FAIL — do not promote.** The Auditor now produces substantive answers in most
cases and the deterministic harness reports 7/7, but that is not a legal-quality
pass. Only case 06 is fully grounded with a precise citation. Cases 01, 02 and
05 recover the core rule but include noisy citations or scope omissions. Case 03
is materially incomplete, case 04 is only a guarded partial abstention, and case
07 avoids a false historical-current claim without identifying the current rule.

The machine-readable case-by-case grade is in
`critical-plan-20260826-post-auditor-independent-grade.json`.

## Reproducibility

- Artifact: `eval/results/critical-plan-20260826-post-auditor`
- Artifact SHA-256: `8c01ebd05be8105e73391910a62e085469ad0f917680aa56fe7519ed9392e67e`
- Fixture: `eval/cases/critical-bhyt-7.jsonl`
- Fixture SHA-256: `461329a9452bd38104f3c78f6e45b53fe0217250d5cca9cbf0f330ddfb2a9ebb`
- Model: `gpt-5.6-luna`

The grade evaluates public document numbers and source text. Internal database
or chunk identifiers were not used as pass criteria.

## Case-level assessment

| Case | Legal correctness | Claim verification | Citation precision | Temporal | Security | Verdict |
|---|---|---|---|---|---|---|
| 01 — 5-year co-pay | Core rule correct; transition unresolved | Partial | 4/10 direct | Partial | Pass observed | Partial, not ready |
| 02 — provincial self-referral | Core rates correct; scope/wording gap | Partial | 2/9 direct | Partial | Pass observed | Partial, not ready |
| 03 — student 2026 | Incomplete, no amount/rate | Partial | 2/10 direct | Partial | Pass observed | Fail |
| 04 — referral validity | Absence claims unobservable; procedures partial | NOT_OBSERVABLE | 3/10 direct | Risk | Pass observed | Partial safe abstention |
| 05 — emergency/no referral | Core emergency rule correct; no-referral detail implicit | Partial | 1/9 direct | Pass for stated rule | Pass observed | Partial, not ready |
| 06 — cosmetic service | Correct | Pass | 1/1 direct | Pass observed | Pass observed | Pass content only |
| 07 — historical 2005 | No current answer | NOT_OBSERVABLE | 0/8 direct | Safe negative only | Pass observed | Fail |

The aggregate direct-support count is approximately 13/58 (`22.4%`). This is a
strict manual diagnostic count: duplicate, unrelated and merely topical
citations do not earn precision credit.

## Important correctness findings

Case 01 correctly states the 100% benefit, five continuous years and the newer
“six times the reference amount” wording, while flagging the older “six months
of base salary” wording. It does not resolve which transition rule applies to a
user's treatment date. Several citations are administrative or unrelated.

Case 02 gives the key 50% outpatient/100% inpatient distinction for the former
provincial tier and 100% former-district rule. It repeats a bullet and says
“mức đóng” (contribution) where the answer needs benefit level/conditions.

Case 03 identifies students as a supported group and the reference-amount basis,
but omits the legally useful contribution percentage and cannot state the 2026
amount or applicable support rate. Local resolutions for specific provinces and
military documents create citation noise for a national question.

Case 04 is a reasonable fail-closed response where the fixture permits
abstention. However, an absence claim (“the source has no duration”) is not
provable from retrieved excerpts, and using the 2008 law without a currentness
check is a temporal risk.

Case 05 quotes the correct emergency rule from 51/2024/QH15. It should explicitly
connect the rule to treatment without a referral and avoid the many unrelated
citations that dilute precision.

Case 06 is the strongest result: the exact official instrument supports Article
23(6), and the answer correctly says cosmetic services are not covered.

Case 07 safely refuses to treat a 2005 circular as current without proof. It
still fails the requested answer because none of its eight citations establishes
the current governing instrument or the historical instrument's status.

## Latency versus PLAN

Observed p50 is `15,761.05 ms` and p95 is `22,784.86 ms` across seven cases. The
Plan targets hybrid p95 ≤8 seconds and temporal/multi-hop p95 ≤15 seconds (or an
async result). The observed run exceeds both diagnostic targets. Since n=7, the
p95 is effectively the maximum and must be confirmed with repeated runs, but it
is already a release blocker. First-useful-event latency is absent.

## Trace completeness — NOT_OBSERVABLE

The artifact includes route intent, retrieval aggregate timing, context size,
candidate/evidence counts and claim counts. It does not include an actual trace
ID or stage-level trace for SQL, embedding, Qdrant, Neo4j, reranking or LLM.
Provider finish reason, token usage, cost, tool/state events and request
error/timeout events are also absent. `provider_observability` is explicitly
`not_exposed_by_runtime`; the deterministic pass therefore cannot be treated as
proof of trace completeness.

## Security

No answer exposed an opaque internal ID or secret. This is a pass for the
returned text only. It does not prove that hidden traces, logs or provider
telemetry are leak-free.

## Required next gates

1. Add citation-support filtering and deduplication before final answer assembly.
2. Add claim-to-citation verification that rejects topical but non-supporting
   passages.
3. Resolve temporal/currentness with effective-date/status evidence, especially
   cases 01, 04 and 07.
4. Add deterministic structured handling for 2026 contribution percentage,
   emergency/no-referral scope and table-like numeric facts.
5. Emit real per-request traces with stage timings, selected spans, provider
   usage and errors; aggregate metadata is insufficient.
6. Repeat the full fixture at least three times and enforce the Plan latency
   gates before promotion. Human legal review remains mandatory.
