# Live RAGAS Evaluation Design

## Goal

Replace the false-positive draft evaluator with one clean, reproducible, read-only evaluation run whose questions and reference answers are derived from the repository's real legal corpus, whose actual answers come from the configured live MediPay GraphRAG runtime, and whose failures explain exactly which metric or hard gate failed.

## Scope and safety boundary

- Read source data from `data/raw/metadata_bhyt.csv`, `metadata_vien_phi.csv`, and `content.csv`.
- Call the configured OpenAI, Supabase/PostgreSQL, and Neo4j dependencies in read-only mode through the existing agent boundary.
- Never call payment, claim, refund, email, profile-update, ingestion, migration, or other write paths.
- Never write credentials, connection strings, authorization headers, or environment values into artifacts.
- Do not modify `src/`, `database/`, `web/`, production prompts, API contracts, schemas, or `requirements.txt`.
- Install RAGAS only into the local evaluation virtual environment if necessary and record its exact version in the run manifest.
- Delete old `eval/results/run-*` and archived run folders only after the new run has complete, validated artifacts.

## Dataset design

The evaluator builds one deterministic `golden_dataset.jsonl` from rows that have a public document number/title and matching real content in `content.csv`.

The primary source-backed set contains 30 single-turn cases balanced across `metadata_bhyt.csv` and `metadata_vien_phi.csv`:

- document title lookup;
- effective date and current-status lookup;
- BHYT/viện-phí domain classification.

Each source-backed case contains a public user question, a deterministic reference answer copied/formatted from source fields, atomic required facts, one or more reference context texts, reference document IDs, source filename/row index, risk, and source hashes. Internal IDs never appear in user questions.

Six additional policy cases cover medical overreach, privacy/authorization, secret disclosure, prompt injection, claim overpromise, and billing without required data. They are marked `synthetic_policy`; RAG retrieval metrics are `N/A` for these cases and deterministic safety gates take precedence.

The source-derived reference is reproducible evidence, but the report must state that no Vietnamese legal/domain expert signed it off during this automated run.

## Runtime and artifact flow

1. Validate source files, hashes, case IDs, non-empty questions/references, reference-context availability, and absence of secret patterns.
2. Send only the user question and permitted runtime context to the existing agent.
3. Save the raw answer, retrieved context text and IDs in rank order, citations, trace ID, latency, status, and redaction marker.
4. Run deterministic checks first.
5. Run official RAGAS metrics with the configured evaluator model.
6. Merge scores, apply per-case gates, aggregate by metric/category/origin, and write failures with evidence.

## Metrics and gates

Source-backed cases receive:

- `factual_correctness`: RAGAS comparison of response to reference;
- `response_relevancy`: RAGAS response relevance to the user question;
- `faithfulness`: RAGAS support of response claims by retrieved contexts;
- `context_precision`: RAGAS ranking/relevance of retrieved contexts;
- `context_recall`: RAGAS coverage of reference claims by retrieved contexts;
- `completeness`: deterministic required-fact recall;
- `id_context_precision` and `id_context_recall`: deterministic document-ID retrieval checks;
- `fallback`, forbidden-claim, runtime-error, and missing-trace flags.

Case quality score:

```text
0.20 factual_correctness
+ 0.15 completeness
+ 0.15 response_relevancy
+ 0.15 faithfulness
+ 0.15 context_precision
+ 0.15 context_recall
+ 0.05 id_context_recall
```

A source-backed case passes only when:

- quality score is at least `0.60`;
- factual correctness, completeness, and context recall are each at least `0.60`;
- the answer is not the generic fallback;
- no runtime, privacy, safety, or forbidden-claim hard failure exists.

RAGAS metric errors are `NOT_OBSERVABLE`, never silently converted to `0`, `1`, or PASS. Safety cases pass only their explicit deterministic behavior assertions. Any P0/P1 safety/privacy failure blocks the run regardless of averages.

## Failure reporting

Every failed case records:

- actual and reference answer;
- all available metric values and threshold comparisons;
- missing required facts;
- target document presence/rank;
- retrieved context IDs/titles;
- failure taxonomy and severity;
- a concise explanation such as retrieval miss, noisy ranking, unsupported answer, irrelevant/fallback answer, incomplete answer, RAGAS error, or safety violation;
- recommended inspection points.

`failures.md` groups cases by root failure category while `case_scores.jsonl` retains full per-case evidence.

## Output layout

Only one current result directory remains under `eval/results/`:

```text
run-<timestamp>/
├── run_manifest.json
├── dataset_validation.json
├── golden_dataset.jsonl
├── actual_answers.jsonl
├── ragas_scores.jsonl
├── case_scores.jsonl
├── summary.json
├── failures.md
└── report.md
```

## Acceptance criteria

- The evaluator has tests that reproduce the prior false PASS and prove a fallback/missing-fact answer fails.
- Golden cases are deterministic and traceable to real source rows/content.
- The live run produces one actual-answer record and one case-score record per case.
- Official RAGAS library/version and evaluator model are recorded.
- All required metrics are present or explicitly `NOT_OBSERVABLE` with an error reason.
- Cases below `0.60` or failing a hard gate appear in `failures.md` with a concrete explanation.
- Aggregate reports cannot claim PASS while factual/grounding metrics are absent.
- Old result folders are removed only after the new run and artifact consistency checks complete.
