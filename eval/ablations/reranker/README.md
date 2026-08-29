# Reranker ablation

Compare lexical/dense RRF, deterministic sentence coverage, and a pinned
cross-encoder on the same candidate artifact. Record Recall@k, citation
precision, route p95 and memory/cost. No variant may be promoted without a
paired legal review and a no-regression run.

The repository harness `evaluate.py` runs the deterministic IR portion without
calling a provider:

```bash
PYTHONPATH=. uv run python eval/ablations/reranker/evaluate.py \
  /absolute/path/candidate-evidence.jsonl \
  --output /absolute/path/reranker-ablation.json
```

The input must be release-locked and contain `case_id`, `query`, `candidates`
(`chunk_id`, `document_id`, text/score) and `relevant_ids`. The output reports
Recall@k/MRR for RRF-score order versus sentence-coverage order. It is not a
legal accuracy result; human review and production latency are separate gates.
