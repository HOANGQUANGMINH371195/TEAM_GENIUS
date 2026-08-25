# Corpus — developer guide

Build a new immutable release from raw/clean inputs with the corpus scripts.
Validate metadata, `answer_ready`, external/reference flags, text hashes,
semantic input hashes and approved graph edges before publishing. Use the
release benchmark builder only after the source manifest is frozen.

```bash
uv run python database/corpus/build_release_benchmark.py --help
uv run python database/corpus/verify_live_corpus_parity.py --help
```

Never mutate the active release in place; cut over through the guarded pointer.
