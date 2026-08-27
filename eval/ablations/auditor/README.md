# Auditor ablation

Compare no-auditor, lexical Auditor, and calibrated three-score Auditor on
claim-level support, authority/currentness, completeness and abstention. Keep
all answer text and source hashes in a redacted review artifact.

The executable harness is `evaluate.py`. Input rows must carry canonical
`source_sha256`, evidence/three-score values, and unanimous labels from at
least two independent reviewers. Disagreement or missing provenance fails
closed; no model-generated label is accepted as gold.

```bash
PYTHONPATH=. uv run python eval/ablations/auditor/evaluate.py \
  /absolute/path/reviewed-claims.jsonl \
  --output /absolute/path/auditor-ablation.json
```
