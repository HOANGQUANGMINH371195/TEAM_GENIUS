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

The research worker is optional tooling and is not part of the AWS request path;
do not block API promotion on that service.

## 2. Produce the mandatory production evidence artifacts

| PLAN item | Required artifact | Who/where it comes from | Closing command |
|---|---|---|---|
| Authenticated cold/warm/concurrency baseline | `production-evidence.json` | Operator against the deployed API with a real auth token | `make collect-production-evidence ENDPOINT=... FIXTURE=... OUTPUT=...` |
| Independent reranker comparison | Reviewed ablation JSON with pinned model/config | Reproducible run plus two-person review of the comparison | Attach to the production attestation; do not infer from unit tests |
| Abstention/clarification calibration | `human-legal-review-v1` labels and calibrator artifact | At least two independent legal reviewers | `make calibrate-claims LABELS_FILE=... OUTPUT=...` |
| Dense/document-graph/live trace comparison | Paired trace report with route, latency and quality deltas | Same release and fixture, independently inspected | Include hashes and paired-run metadata in attestation |
| AWS host/deploy/recovery | Ansible transcript, TLS/readiness, rollback and restore report | Operator with the AWS account and the active release | Run the single-host drill; attach logs and image/release digests |

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

The repository cannot create these without explicit operator authorization;
they are **optional** and are not part of the AWS production request path:

- a managed durable Redis instance or a separate research-worker service;
- accepted, provenance-hashed typed BHYT facts for a future fact-graph release;
- provider batch receipts reconciled to the cost ledger.

The following remain blocking because they directly measure the chosen release
axes:

- authenticated AWS endpoint cold, warm, concurrency, outage and rollback runs;
- independent legal review of accuracy and citation support;
- Qdrant active-collection configuration and Neo4j release parity evidence.

For the current release, resolve the data-plane blockers in this order:

1. Read `active_dataset_id` and its Qdrant projection locator from PostgreSQL;
   set the host `QDRANT_COLLECTION` to that locator and re-run `/ready`.
2. Run `database/corpus/verify_live_corpus_parity.py` in read-only mode against
   the pinned source/release lock. If Neo4j differs, create a backup and use
   `database/neo4j/scripts/cleanup_stale_release.py --dry-run`; never delete the
   active release or repair counts by hand.
3. Repeat `/ready`, one authenticated `/chat`, one `/chat/stream`, and the
   rollback drill. Attach the raw JSON reports to the attestation.

Until those artifacts exist, `PLAN.md` must retain unchecked boxes and the
system must not be described as production-ready or SOTA.
