# Insurance & Hospital Billing Golden Evaluation Design

## Goal

Create a non-invasive, repeatable evaluation harness that derives a synthetic
`draft_gold` dataset from the repository's public legal corpus, validates it,
attempts actual-answer generation only when an isolated agent runtime is
explicitly configured, and produces actionable case-level diagnostics.

## Constraints

- Do not modify production runtime code, API contracts, prompts, schemas, dependencies, CI, or databases.
- Do not send real patient, payment, secret, or authentication data to an agent or artifact.
- Do not call the configured database, OpenAI API, Neo4j, payment, claim, refund, or email side effects unless an explicit isolated evaluation mode is configured.
- Auto-generated gold is `draft_gold` and cannot establish a release PASS without domain review.
- Outputs go to a unique directory under `C:\tmp\agent_eval\P-151\`.

## Design

The harness has four independent layers:

1. **Dataset builder** reads `metadata_bhyt.csv` and `metadata_vien_phi.csv`,
   selects deterministic public-document records, and creates document lookup,
   effective-date, category, missing-information, safety, privacy, and prompt-
   injection cases. Gold facts are copied from source rows or explicitly marked
   synthetic policy expectations.
2. **Dataset validator** checks IDs, inputs, source references, hash manifest,
   draft-gold labels, absence of obvious secrets/PII, and required metadata.
3. **Actual-answer adapter** records one result per case. It refuses to call a
   live runtime unless `EVAL_AGENT_MODE=isolated` and an explicit adapter is
   available; otherwise it writes `not_observable` with the exact blocker.
4. **Deterministic report generator** scores available answers, separates
   `PASS`, `FAIL`, and `NOT_OBSERVABLE`, applies hard safety/privacy gates, and
   maps failures to likely production inspection points.

The generated dataset deliberately separates fields sent to the agent from
gold/evaluator-only fields. The evaluator never passes expected facts,
forbidden claims, or rubric into the agent input.

## Output

Each run contains `run_manifest.json`, `dataset_validation.json`,
`draft_gold.jsonl`, `actual_answers.jsonl`, `case_scores.jsonl`, `summary.json`,
`failures.md`, and `report.md`. The human report explains what was checked,
what was not observable, and where an owner should inspect code.

## Acceptance criteria

- Dataset generation is deterministic for the same source-file hashes.
- All generated cases validate and contain no real secrets or patient data.
- A missing runtime produces a complete denominator of `not_observable` records,
  not a false pass.
- Offline evaluator runs without production dependencies or network access.
- Every reported failure includes a category, severity, evidence reference, and
  recommended production inspection location.
