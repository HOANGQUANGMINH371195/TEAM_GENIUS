# MediPay — Accuracy × Latency × Market Leadership Plan

> Canonical execution plan as of 2026-08-26. Historical audits, completed-work
> logs, measurements, and previous decisions live in `AUDIT.md`.
>
> A repository capability is implementation-complete only when its code path,
> configuration, focused acceptance test, and rollback/failure behavior exist.
> Production capability is promotion-complete only after the independent
> benchmark, live latency test, production rollout, and rollback evidence pass.
> These are separate gates: benchmark evidence is collected only after the
> implementation gate passes and is never itself production approval.

## 1. Product objective

MediPay must become a **BHYT Decision & Evidence Engine**, not another generic
legal chatbot. The user-visible differentiation is:

1. Answer the law at the requested date, including amendment, replacement,
   exclusions, and applicable authority.
2. Calculate BHYT benefits and patient cost deterministically from structured
   legal tables and user conditions.
3. Show `conclusion → conditions → exceptions → legal basis → original HTML`
   with a deep link to the exact provision or table row.
4. Maintain private, bounded conversation context per user while re-retrieving
   current evidence on every legally material turn.
5. Explain uncertainty and ask only for missing facts that could change the
   legal outcome.

North-star gates:

| Metric | Production gate |
|---|---:|
| Critical BHYT legal accuracy | ≥95% human-adjudicated; zero catastrophic error |
| High-risk claim supported by citation | ≥98% |
| Numeric/table calculation exactness | 100% deterministic fixtures |
| Simple route latency | p50 ≤2 s; p95 ≤5 s |
| Hybrid topical latency | p50 ≤4 s; p95 ≤8 s |
| Temporal/multi-hop latency | p95 ≤15 s or async job |
| First useful SSE event | p95 ≤1 s |
| Availability | ≥99.5% |
| Cost | ≥30% reduction from the locked baseline without accuracy loss |

## 2. Current state and gaps

Current runtime is **hybrid legal RAG with optional graph expansion**:

```text
PostgreSQL exact/lexical/PageIndex + Qdrant dense/hybrid
→ RRF/heuristic reranking
→ optional Neo4j document/reference expansion
→ LLM generation + claim/citation guard
```

Present foundations:

- Release-scoped canonical corpus with provenance and content hashes.
- Exact, lexical, PageIndex, Qdrant, and bounded Neo4j retrieval.
- Firebase-authenticated conversation/turn persistence by `owner_uid`.
- Safe SSE envelope, release-aware local caches, provider guards, and evals.
- Offline embedding batches and bounded subquery embedding/Qdrant batches.

Implementation is now present for the calculator, sanitized viewer, private
cache, typed-fact projection, route budgets, bounded grounded planning, claim
uncertainty schema, and immutable batch manifests. The remaining gaps are
validation gates rather than missing modules: independent human labels,
authenticated production latency/outage evidence, learned-reranker ablation,
live projection parity, and reconciled provider-batch cost records. These
must stay visible until their evidence artifacts exist; code presence alone is
not a promotion decision.

Managed-data constraints:

- Supabase retains only active release `snapshot-c439751724ab7f10`.
- Full staging/shadow copies must not be retained in the free-tier database.
- `table_cells` is canonical; `table_cell_facts` is a rebuildable projection.
- Qdrant and Neo4j projections must remain release-scoped and rebuildable.

## 3. Target online architecture

The router chooses the cheapest correct path. It must not call every database
for every query.

```text
query + private conversation anchors
  → normalize and resolve references
  → typed RoutePlan
  ├─ policy/casual      → deterministic response
  ├─ exact/legal-unit  → PostgreSQL/PageIndex → formatter
  ├─ table/numeric     → structured facts → calculator → verifier
  ├─ topical           → lexical + Qdrant → sentence reranker
  ├─ temporal          → currentness filter + bounded graph → hydrate
  ├─ relational        → typed graph seeds + bounded PPR → hydrate
  ├─ global            → curated community summaries → canonical evidence
  └─ deep              → async grounded-planning job
        → coverage selector
        → claim-first generation
        → Auditor + uncertainty
        → citation/HTML anchors
```

`RoutePlan` contract:

```text
route, risk, required_facts, providers,
retrieval_budget_ms, generation_budget_ms,
max_candidates, context_budget, verifier_policy
```

Non-negotiable invariants:

- PostgreSQL text/HTML is legal truth. Qdrant, Neo4j, summaries, and memory are
  projections/navigation hints, never citations by themselves.
- Every graph/vector result used in an answer is re-hydrated to a canonical
  passage with matching release and hash.
- Optional graph, rewrite, community, and document-recall failures degrade to
  a valid primary route; they do not fail the whole chat.
- Exact and table routes avoid embedding/LLM calls when a formatter is enough.
- High-risk conclusions are not emitted before verification passes.

## 4. Accuracy engine

### 4.1 Typed BHYT ontology and Auditor (optional future projection)

Use a small, reviewed, versioned ontology:

```text
BeneficiaryGroup, ParticipationPeriod, CareEvent, HospitalLevel,
ReferralStatus, EmergencyCondition, CoverageRate, CopaymentThreshold,
Exclusion, EffectiveInterval, LegalProvision,
Amends, Replaces, RefersTo
```

Every fact stores:

```text
fact_id, subject, predicate, normalized_value,
effective_from/to, jurisdiction, provision_id,
document_id, unit_id, source_span/hash,
review_status, release_id
```

The Auditor validates each claim for evidence, beneficiary, conditions,
effective date, jurisdiction, exclusions, current authority, numeric trace, and
canonical citation. Unsupported claims are removed or downgraded.

The current release does not require a newly extracted typed-fact corpus;
canonical PostgreSQL text/HTML and existing table cells remain the legal
source of truth. The ontology and projection path stay available for a future
reviewed release.

### 4.2 Retrieval and reranking

- Run signature/article/date/status filters before vector search.
- Keep learned sparse/BM25 and dense retrieval as independent channels.
- Use RRF only for candidate fusion; retain original rank/score metadata.
- Rerank the top 20–30 passages at sentence level, selecting 6–10 compact
  evidence blocks for generation.
- Preserve coverage of beneficiary, condition, rate, exception, and date.
- Run bounded PPR only from typed seeds on relational/multi-hop routes.
- Hydrate every selected fact/path back to canonical source text.

### 4.3 Grounded planning

Only enable planning when preliminary retrieval reveals at least two missing
knowledge items:

```text
preliminary retrieval
→ evidence inventory
→ explicit missing facts
→ retrieve only missing facts (fan-out ≤3, depth ≤2)
→ stop on coverage or budget
```

Blind decomposition and HyDE are not default. Each additional branch must add
new evidence; duplicate branches are cancelled.

### 4.4 Claim uncertainty

Calibrate three separate scores:

- Faithfulness: supported by retrieved evidence.
- Factuality: valid under current authority and deterministic checks.
- Completeness: enough conditions/exceptions are known for a conclusion.

Low confidence produces a precise clarification or abstention reason, never a
generic “no information” response.

## 5. Structured table intelligence and calculator

```text
sanitized raw HTML
→ merged-cell-aware table extraction
→ subject–attribute–value–unit–effective interval facts
→ typed normalization
→ deterministic calculator
→ legal/currentness verification
→ grounded explanation
```

Requirements:

- `table_cells` remains canonical. A derived typed-fact projection is optional
  and deferred; it must not block the current release or trigger new extraction.
- Use Decimal/rational arithmetic, never float, for money and percentages.
- Inputs include beneficiary, salary base, facility level, referral/emergency,
  participation duration, treatment date, and covered cost.
- Every intermediate result exposes formula ID, inputs, units, and provenance.
- Missing material input triggers a clarification form; no guessed defaults.
- Qdrant retrieves semantically relevant rows but never decides exact values.
- Calculator unit/property/golden tests must reach 100% before public release.

## 6. Original HTML document viewer

Deliver a production endpoint and UI for citation-to-source inspection:

- Public document key only; no internal dataset/chunk IDs.
- Hash verification before rendering.
- Allowlist sanitation for headings, paragraphs, lists, tables, and anchors;
  strip scripts, styles, handlers, frames, and active external content.
- Isolated rendering with restrictive CSP.
- Deep-link and highlight exact article, paragraph, span, or table row.
- Display document number, issuer, status, effective interval, and official URL.
- Support side-by-side answer/calculator trace and original provision.

Acceptance: zero XSS fixture, ≥99% citation-anchor resolution, and warm viewer
p95 ≤1 second.

## 7. Per-user conversation context and cache

Do not use a 1M-token context as default memory. Use bounded layered memory:

```text
durable PostgreSQL:
  turns + citations + release + anchors + explicit structured user facts

shared Redis:
  owner_uid + conversation_id
  → bounded summary + resolved anchors + last N turns

request context:
  current query + relevant prior turns + freshly retrieved evidence
```

Rules:

- Isolation key is `owner_uid + conversation_id`; no shared raw-history cache.
- Summary is navigation context, not evidence or a legal conclusion.
- Previous citation anchors are resolved and retrieved again for the active
  release before reuse.
- Default budget: last 6–10 turns, ≤2k-token summary, ≤1k-token anchors.
- Release/auth/schema changes invalidate the private context cache.
- Support retention, TTL, RLS, user deletion/export, and encrypted transport.

Shared cache layers:

| Layer | Key | Policy |
|---|---|---|
| Metadata | release + document | immutable/long TTL |
| Embedding | model + normalized query | 5–30 min |
| Retrieval | release + route + policy hash + query | 1–10 min |
| Conversation | owner + conversation + release | short/private |
| Final answer | release + verified context digest | low-risk only |

All caches require bounded size, single-flight, stale/hit/miss telemetry, and a
correctness-preserving fallback.

## 8. Batch and cost strategy

Batch offline work; do not delay interactive final answers to wait for users.

| Workload | Policy |
|---|---|
| Corpus embeddings | Offline batches 128–512, resumable by content hash |
| Fact/metadata extraction | Provider Batch API with immutable manifest |
| Eval/golden judging | Provider Batch API with pinned model/config |
| Multi-query in one request | One embedding batch + Qdrant batch |
| PostgreSQL/Neo4j hydration | Multi-ID reads; no N+1 |
| Reranker | Micro-batch with ≤20 ms queue budget |
| Final interactive chat | Never batch across users |

Every job has idempotency, checkpoint, cost estimate, partial retry, release
hash, and poison-item quarantine. Projection registry changes to ready only
after all stores flush and parity passes.

Cost reduction order:

1. Avoid LLM on exact/table routes.
2. Cache and single-flight repeated work.
3. Sentence selection reduces context tokens.
4. Batch offline provider work.
5. Use a small planner/extractor and the main model only for difficult synthesis.
6. Move deep research to cancellable async jobs.

## 9. Market-leading product surface

Ship these in order:

1. **BHYT Benefit Calculator** — deterministic scenarios, effective-date aware,
   with citation for every rule and formula input.
2. **Legal Evidence Timeline** — original rule, amendment/replacement chain,
   rule at date X, and click-through HTML evidence.
3. **Eligibility Checklist** — asks only for facts that change the outcome and
   explains required documents/referral/emergency conditions.

After those pass production gates:

- Compare two hospitals/treatment scenarios.
- “What changed?” legal diff between two dates.
- Admin conflict/review queue for low-confidence extraction.
- Opt-in corpus-change impact notifications.

## 10. Research intake decisions

| 2026 pattern | Decision |
|---|---|
| HCG-RAG schema-constrained graph | Typed BHYT graph PoC; require accuracy/build-cost win |
| GPS-RAG grounded planning | Multi-hop only; evidence-gap driven and budgeted |
| LongEval sentence reranking | P0 ablation on Vietnamese legal evidence |
| FRANQ uncertainty | Claim factuality/faithfulness/completeness contract |
| Learned sparse + listwise reranking | Test offline; do not LLM-rerank every query |
| Experience retrieval | Reviewed, de-identified trajectories only |

References:

- https://arxiv.org/abs/2607.22592
- https://openreview.net/pdf?id=Gfna03MpkW
- https://arxiv.org/abs/2607.04008
- https://aclanthology.org/2026.findings-acl.338/
- https://arxiv.org/abs/2606.11945
- https://arxiv.org/abs/2603.18272

## 11. Evaluation and promotion law

Three independent gates:

1. Deterministic retrieval/calculator suite without LLM judges.
2. Human-adjudicated legal answers with claim, authority, date, and exception.
3. Authenticated live provider/load suite on Render/Vercel.

Minimum dataset:

- 300 release-locked core questions.
- 100 adversarial paraphrase/near-miss questions.
- 100 table/numeric/scenario fixtures.
- 75 temporal/amendment/repeal cases.
- 75 multi-turn/reference-resolution cases.
- 50 unanswerable/ambiguous/injection cases.

Each run records route, stage latency, candidates before/after rerank, graph
paths, tokens, claims, citations, confidence, cost, and failure reason.

A change is promoted only when:

- Exact/policy/calculator gates do not regress.
- Critical legal accuracy does not fall for a latency improvement.
- Gains repeat across at least three runs with paired analysis.
- Cold/warm/concurrency p95 meets route budgets.
- Optional-provider outage passes degraded-mode tests.
- A feature flag, canary, migration/rebuild artifact, and rollback exist.

## 12. Delivery roadmap

## 12.0 Execution status — 2026-08-27

This section is the authoritative implementation ledger. A checked item means
the code path and a focused verification artifact exist; it does **not** mean
the production gate in Section 1 has passed. `make implementation-gate` is the
pre-benchmark implementation gate; human labels, live provider measurements,
and rollback evidence are collected only after that gate passes.

| Area | Current evidence | Status |
|---|---|---|
| Local/managed readiness | Docker Desktop `local-full` profile started from `/home/minh/projects/team-Vin-genius/.env`; local Postgres, Qdrant, Neo4j, Redis, API and web containers healthy. Additive migrations `20260824_document_lexical_index` and `20260827_typed_legal_facts` applied; API/web/migrate images rebuilt from current source; `make build` and `make check ENV_FILE=.../.env` passed; 20-request local `/ready` smoke p95 124.5ms, all 200. Read-only Render CLI metadata on 2026-08-28 confirms service `medipay-api` is Docker/starter/Oregon/main with `/health`; direct read-only probes to `/health` and `/ready` returned 200 with database, Qdrant, Neo4j, LLM and embedding ready. No `medipay-research-worker` exists, and the only listed Render Redis is free but suspended, so the durable worker is not provisioned. | local/managed API readiness is evidenced; durable worker/Redis provisioning and authenticated managed latency/outage/rollback attestation remain external |
| Typed route contract | `src/domain/route_plan.py`, route metadata in intake, provider allow-list, route-scoped candidate/context caps, retrieval fallback deadline and generation timeout. Query-shape routing now distinguishes policy/exact/table/topical/temporal/relational/global/deep without mapping a legal topic to an answer. | implemented; production calibration/latency proof pending |
| Stage telemetry | `retrieval_trace`, `planner_ms`, `verification_ms`, `guardrail_ms`, provider/generation timers, allowlisted Langfuse stage export, browser TTFT event, `eval/collect_production_evidence.py` (route-specific exact/table/topical/temporal/relational p95 plus TTFT); production attestation now hash-binds the collector JSON and compares every metric | repository telemetry/export and evidence collector implemented; managed dashboard and authenticated browser TTFT proof pending |
| Evidence-gap planning | `src/services/planner.py`, bounded fan-out/depth follow-up retrieval on relational/temporal/deep routes, deterministic direct-vs-planned ablation harness | implemented; independently reviewed completeness/latency evidence is a post-implementation benchmark gate |
| Claim uncertainty contract | `LegalClaim.uncertainty` with faithfulness/factuality/completeness fields, `eval/calibration.py` metrics, independent-panel validator requiring complete labels from at least two reviewers, monotone calibrator artifact CLI, and Auditor ablation harness | implemented; reviewed calibration labels and approval are a post-implementation evidence gate |
| Exact/lexical/dense/PageIndex retrieval | `src/services/chat.py`, `src/db/repositories.py`, current-authority and query-scope filters, release-scoped cache policy. Lexical passage recall now scopes to query-derived document candidates, uses indexed `search_vector`, and bounds full-text candidates before ranking. Local smoke: 0.55–0.80s passage search; document recall 1.3–2.8s across tested BHYT queries. | implemented with generic scope/currentness filtering and synthesis-only high-risk answers; production latency/quality gate not proven |
| Answer synthesis safety | `SYSTEM_PROMPT`, model-only synthesis for multi-passage high-risk requests, duplicate-line removal, raw-chunk detector in guardrail | repository invariant implemented; live provider regression proof pending |
| Optional graph degradation | guarded temporal/relational expansion with route-deadline-bounded Neo4j calls and lexical+dense fallback | implemented; managed outage/load proof pending |
| Decimal calculator | `src/services/calculator.py`, two registered Decimal formulas, `/calculator/bhyt`, bounded `/calculator/bhyt/scenarios`, `web/app/calculator/page.tsx`, reviewed `table_cell_facts` retrieval, `eval/cases/calculator-golden-v1.jsonl` (100 cases), API scenario tests | exact arithmetic, 100-case golden, bounded API, and UI build acceptance implemented; live table-source parity remains external |
| Sanitized HTML viewer | `/documents/{public-signature}/html`, `document_viewer.py`, `web/app/document/page.tsx`, `tests/test_api/test_document_viewer_endpoint.py` | hash-verified backend/UI and local XSS/anchor/path-integrity acceptance implemented; managed smoke remains external |
| Legal evidence timeline | `/legal/timeline`, canonical PostgreSQL metadata hydration, bounded two-hop Neo4j navigation with degraded fallback, `web/app/timeline/page.tsx` | repository endpoint/UI/public-ID contract implemented; managed graph and anchor acceptance remain external |
| Eligibility checklist | `/eligibility/checklist`, conditional user-fact dependency engine, owner-scoped `conversations.facts` migration and `web/app/eligibility/page.tsx`; responses never contain a legal decision or echo fact values. Migration `20260834_conversation_facts` applied through the locked runner to the `.env` PostgreSQL on 2026-08-28; read-only schema/ledger check confirms column and head. | repository plus managed schema contract implemented; legal retrieval remains mandatory after completion |
| Private conversation cache | `conversation_cache.py`, Redis/in-memory fallback, single-flight, hit/miss metrics, release-scoped keys, Redis failure fallback test | repository failover contract implemented; production Redis latency/availability proof pending |
| Feature flags | `FEATURE_*` settings and rollout switches | implemented |
| Sentence-level rerank seam | query-derived sentence coverage plus opt-in `src/services/reranker.py` cross-encoder backend; deterministic `eval/ablations/reranker/` harness | implemented; pinned-model result and latency are post-implementation evidence |
| Typed BHYT fact contract | Ontology/schema/loader/importer remain available as an optional future projection. The current release deliberately uses canonical PostgreSQL text/HTML and existing `table_cells`; no new fact extraction or accepted-fact corpus is required. A read-only primary probe and independent subagent cross-check on 2026-08-28 report `legal_facts_total=0`, and managed Neo4j has no typed-fact labels. | deferred by product decision; not a current-release gate and must not block benchmark or deployment |
| Source/release parity | The default current builder correctly fails closed because it rebuilds a different fingerprint (`snapshot-037cca…`, `38,316`/`14,968`) than the active release. Re-running with the exact builder recorded in `docs/data/release-lock-snapshot-c439751724ab7f10.json` (`source_commit=1b98f44`) passes fingerprint, canonical counts, PostgreSQL/Qdrant counts and hashes, and Neo4j identity/edge parity. The stale `snapshot-c94d7b75195a67fa` projection was backed up and deleted on 2026-08-27; the prior `live_parity.json` remains stale evidence. | exact-builder parity and stale-projection cleanup verified; immutable artifact retention and deployment attestation remain required before production promotion |
| Grounded planning/uncertainty calibration | bounded grounded planner, direct-vs-planned ablation harness, `eval/calibration.py` independent-panel validation and dependency-free pool-adjacent-violators fitting, `eval/calibrate_claims.py` hashed review artifact | implemented; human-labelled calibration and approval are post-implementation evidence |
| Batch extraction/eval manifests | offline embedding/Qdrant batching, immutable `eval/batch_manifest.py`, provider JSONL, authenticated adapter/reconciliation in `eval/openai_batch.py`, hash-bound `cost-ledger-v1` attestation artifact | repository submission/reconciliation contract implemented; live provider cost proof pending |
| Release-locked data artifacts | Active release benchmark files mounted from the external `data/clean/medical_active_v31_fully_reviewed` artifact store; SHA-256/hash and coverage suite now passes (`eval/test_release_locked_suite.py`: 2 passed). `scripts/verify_release_artifacts.py` now reports `available=false, verified=false` for source-only checkouts and fails closed on missing/mismatched files when required; the nightly workflow records the contract and runs Qdrant evaluation only when artifacts are mounted. Artifacts remain ignored and are never committed to the source checkout. | local provenance gate passed; a release job still must mount and attest the immutable artifact |
| Production gates | no paired cold/warm/concurrency + human adjudication release | not passed |

Do not run or publish a model benchmark before `make implementation-gate` and
the deterministic suites pass. `make promotion-gate` now reports two separate
results: benchmark collection may proceed after implementation blockers are
zero, while production promotion remains blocked until the independent gates
in Section 1 and `verify_production_attestation.py` pass.

### Phase A — Fast stable baseline (P0, 3–5 days)

- [x] Add stage timers for planner, SQL, embedding, Qdrant, hydration, Neo4j,
      rerank, generation, verifier, and browser TTFT.
- [x] Move optional document recall out of the critical failure path.
- [x] Enforce per-route provider/candidate/context/deadline budgets.
- [x] Add Redis private-context/retrieval cache and single-flight.
- [x] Prewarm dependencies at startup; managed-region RTT/pool saturation audit remains an external gate.
- [ ] Lock authenticated live cold/warm/concurrency baseline.

Exit: simple p95 ≤5 s, topical p95 ≤8 s, stream errors <1%, no accuracy loss.

### Phase B — Sentence reranker and Auditor (P0/P1, 5–8 days)

- [x] Build deterministic benchmark candidate/evidence artifact validators and ablation harnesses.
- [ ] Assemble the required independently reviewed artifacts and run the pinned MiniLM/BGE/cross-encoder comparison.
- [x] Implement typed claim and three-score uncertainty contracts.
- [ ] Calibrate abstention/clarification with human labels.

Exit: ≥3-point critical claim-support gain or ≥10% retrieval-precision gain;
added p95 ≤1.5 seconds.

### Phase C — Calculator and HTML viewer (P0 product, 7–12 days)

- [x] Validate merged-cell extraction and typed facts (`database/pipeline/tests/test_tables.py`, release recognizer/importer tests).
- [x] Implement formula registry and 100 golden calculations.
- [x] Expose sanitized, hash-verified HTML and citation deep links.
- [x] Ship scenario comparison and eligibility clarification UI.
- [x] Ship the public-ID-only legal evidence timeline with canonical hydration and graph-outage degradation.
- [x] Ship the deterministic eligibility checklist that collects user facts without deciding the law.

Exit: calculator 100%, zero XSS, anchor ≥99%, table route p95 ≤5 seconds.

### Phase D — Bounded document graph (typed-fact projection deferred)

- [x] Keep the existing release-scoped document/reference graph and bounded
      expansion with canonical hydration and outage fallback.
- [x] Defer typed BHYT fact extraction/projection; no new corpus is required
      for this release. The ontology/importer remain isolated for a future,
      independently reviewed opt-in release.
- [ ] Compare reviewed live traces against dense and current document-graph baselines.

Exit: significant multi-hop gain, graph-path precision ≥95%, p95 ≤15 seconds,
and Neo4j-outage degraded mode passes.

### Phase E — Grounded planning/global research (P2, gated)

- [x] Grounded-planning PoC with fan-out ≤3 and depth ≤2.
- [x] Add deterministic release-scoped community summary builder and bounded DRIFT-style selector (`src/services/global_retrieval.py`, `database/corpus/build_community_index.py`); summaries remain navigation hints and must be hydrated from PostgreSQL.
- [x] Add a bounded owner-isolated async research worker contract (`src/services/research_jobs.py`) with timeout, cancellation and shutdown handling.
- [x] Implement the Redis durable worker, dedicated non-root `Dockerfile.worker` (no HTTP healthcheck), optional Render blueprint, owner isolation, restart/cancellation contract, and runbook.
- [ ] Provision/deploy the worker on a durable queue, wire curated index jobs to it, and compare against the fast hybrid baseline.
- [x] Implement reviewed/de-identified experience retrieval as navigation-only hints.

Exit: completeness gain within async cost budget; reject if the fast hybrid
baseline plus sentence reranker remains better.

## 13. Non-goals

- No full rewrite to an outsource framework.
- No calling every database/model for every query to appear “GraphRAG”.
- No 1M-token default conversation memory.
- No LLM-controlled money/percentage arithmetic or legal-fact mutation.
- No unsanitized raw HTML in the browser.
- No cross-user batching of final interactive answers.
- No full staging/shadow snapshot retained in Supabase Free.
- No SOTA claim from machine-gold or self-generated evaluation alone.

## 14. Required deliverables

- `docs/architecture/route-contract.md`
- `docs/product/calculator-contract.md`
- `docs/product/document-viewer-security.md`
- `docs/product/legal-timeline.md`
- `docs/product/eligibility-checklist.md`
- `docs/data/typed-bhyt-ontology.md`
- `docs/data/release-lock-snapshot-c439751724ab7f10.json`
- `eval/cases/market-leadership-v1.jsonl`
- `eval/results/baseline-live-<timestamp>/`
- `eval/ablations/{reranker,auditor,typed-graph,grounded-planning}/`
- `ops/runbooks/{release,rollback,cache,provider-outage,supabase-retention}.md`
- Feature flags for planner, reranker, Auditor, calculator, viewer, and graph.
- `eval/calibrate_claims.py` and an approved `claim-calibration-v1` artifact for
  confidence-to-abstention calibration.
- `scripts/verify_production_attestation.py` for fail-closed validation of the
  human, latency, outage, ablation, cost and rollback evidence bundle.
- `scripts/verify_implementation_gate.py` to ensure all repository capabilities
  exist before any live benchmark is started.
- `scripts/verify_release_artifacts.py` for fail-closed CI verification of the
  externally mounted release benchmark hashes and locked-suite coverage.
- `eval/build_review_packet.py` and `eval/human_review.py` for redacted,
  answer-hash-bound independent legal review artifacts.

Current file-level delivery evidence (2026-08-27): route, calculator, viewer,
ontology, batch, cache, release/rollback, provider-outage, Supabase-retention
contracts, deterministic Auditor/typed-graph/planning/reranker ablation
harnesses, reviewed-trajectory safeguards, answer-review packet/validator,
reviewer-enforced plan-suite compiler, hash-bound ablation/latency/operations/
cost attestation,
and implementation/promotion gates now exist. Ablation result artifacts, the
human-labelled calibration set, durable
queue provisioning, and paired production benchmark are still absent; they
remain evidence/deployment blockers rather than being marked complete by
documentation alone.

Definition of done: the three differentiated product features run in
production; human accuracy, latency, and cost gates pass; rollout has canary
and rollback; and no claimed capability exists only in this plan.
