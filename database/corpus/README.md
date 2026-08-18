# Reconciled BHYT / viện phí corpus

`build_active_corpus.py` combines the curated CSV authority files with the two
active JSON exports. It never trusts JSON `content_text`; visible text is built
later from selected HTML by the canonical pipeline. Selected `content_html` is
serialized without whitespace normalization, then both its raw SHA-256 and the
regenerated visible-text SHA-256 are rechecked from the written CSV.

Run from the repository root:

```bash
python3 database/corpus/build_active_corpus.py
```

The default output is `data/clean/medical_active_v2/`. Four damaged/missing
documents are recovered from reviewed HTML sources and checked against
official metadata. Cached fragments make subsequent builds reproducible:

```bash
python3 database/corpus/build_active_corpus.py --offline
```

Important outputs:

- `metadata.csv`, `content.csv`, `relationships.csv`: candidate authority input
  for the canonical pipeline;
- `aliases.csv`: old/duplicate IDs mapped to one legal identity;
- `source_provenance.csv` and `recovery_cache/`: evidence lineage;
- `quality_issues.csv`, `crawl_backlog.csv`, `rejected_records.csv`,
  `reference_nodes.csv`: explicit
  unresolved data rather than silently fabricated values;
- `build_manifest.json`: input and artifact hashes plus release counts.

Do not overwrite `data/raw/` or publish to Supabase until the candidate passes
the canonical build and storage-capacity gates.
