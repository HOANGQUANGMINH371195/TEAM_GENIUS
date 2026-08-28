# PLAN completion runbook

This runbook is the closing checklist for `PLAN.md`. It separates repository
implementation from evidence that can only be produced by an authenticated
operator or an independent reviewer. No placeholder, synthetic score, or model
self-judgement may be used to close a gate.

## 1. Verify the repository gate first

Run from the P-151 checkout:

```bash
make check ENV_FILE=/path/to/.env
make implementation-gate
make verify-plan
```

All three commands must pass. `implementation-gate` only proves that the code
paths and focused contracts exist; it does not prove production readiness.

For the local durable-worker preflight (no provider call is made), verify the
image and process contract before requesting managed infrastructure:

```bash
docker compose --profile local-full --profile research-worker build research-worker
docker image inspect medipay-research-worker:latest \
  --format 'user={{.Config.User}} cmd={{json .Config.Cmd}} health={{json .Config.Healthcheck}}'
docker compose --profile local-full --profile research-worker up -d research-worker
docker compose --profile local-full --profile research-worker ps research-worker
docker compose --profile local-full --profile research-worker stop research-worker
```

The expected image invariants are non-root user `65532:65532`, command
`-m src.research_worker`, and a null healthcheck. This local proof does not
close the managed worker/Redis promotion gate.

## 2. Produce the five outstanding evidence artifacts

| PLAN item | Required artifact | Who/where it comes from | Closing command |
|---|---|---|---|
| Authenticated cold/warm/concurrency baseline | `production-evidence.json` | Operator against the deployed API with a real auth token | `make collect-production-evidence ENDPOINT=... FIXTURE=... OUTPUT=...` |
| Independent reranker comparison | Reviewed ablation JSON with pinned model/config | Reproducible run plus two-person review of the comparison | Attach to the production attestation; do not infer from unit tests |
| Abstention/clarification calibration | `human-legal-review-v1` labels and calibrator artifact | At least two independent legal reviewers | `make calibrate-claims LABELS_FILE=... OUTPUT=...` |
| Dense/document-graph/live trace comparison | Paired trace report with route, latency and quality deltas | Same release and fixture, independently inspected | Include hashes and paired-run metadata in attestation |
| Durable research worker | Managed Redis + Render worker deployment and parity report | Operator with permission to incur managed-service cost | Validate Render blueprint, run worker drill, then attach rollback/outage evidence |

The human review packet must be built from redacted answers:

```bash
python eval/build_review_packet.py \
  --fixture <fixture.jsonl> \
  --answers <answers.jsonl> \
  --release-id snapshot-c439751724ab7f10 \
  --output <review-packet.jsonl>
```

Reviewers fill labels independently. `eval/human_review.py` rejects incomplete
panels, disagreement, release mismatches, and answer-hash changes.

## 3. Validate, then promote

Create an operator attestation from the real artifacts and run:

```bash
make verify-attestation ATTESTATION_FILE=ops/attestations/<release>.json
make promotion-gate
```

Promotion is allowed only when `production_promotion_allowed` is `true`, the
attestation verifier reports no errors, and the release/rollback runbook has
been executed. A green deterministic test suite alone is insufficient.

## 4. Current known external prerequisites

The repository cannot create these without explicit operator authorization:

- a non-suspended durable Redis instance and a Render research-worker service;
- authenticated Render/Vercel cold, warm, concurrency, outage and rollback runs;
- two independent legal reviewers and their signed labels;
- accepted, provenance-hashed typed BHYT facts for the active release;
- provider batch receipts reconciled to the cost ledger.

Until those artifacts exist, `PLAN.md` must retain unchecked boxes and the
system must not be described as production-ready or SOTA.
