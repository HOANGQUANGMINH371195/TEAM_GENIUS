# Audit — developer guide

Audit scripts are read-only by default and inspect release manifests, metadata,
serving status and provenance. Run them against a copied artifact directory;
never overwrite `database/audit/results` during an active release cutover.

```bash
uv run python database/audit/audit_medical_corpus.py --help
```

Record the input SHA, dataset ID and output artifact SHA. A failed audit blocks
promotion; it is not evidence that the model is inaccurate.
