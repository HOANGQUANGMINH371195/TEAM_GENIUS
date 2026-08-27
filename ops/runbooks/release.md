# Release publication runbook

This runbook is the only safe path for publishing a new corpus release. A
release ID is a content fingerprint; it must never be reused after the source
files, parser, table extractor, or chunker change.

## Prepare

1. Build the candidate in an isolated output directory and run
   `validate_candidate.py`.
2. Record the generated `dataset_id`, `canonical_validation.json`,
   `build_manifest.json`, source file hashes, and embedding manifest in the
   release ticket. Keep these artifacts private and immutable.
3. Run the deterministic suites and calculator/viewer/security checks. Do not
   activate a candidate with a failed warning that affects legal content.

## Stage and project

1. Stage PostgreSQL under the new release ID; never mutate the active release.
2. Build or reuse embeddings only when every passage ID and input SHA-256
   matches the candidate manifest.
3. Load Qdrant and Neo4j using the same release ID. Neo4j imports must retain
   canonical provenance and approved serving status.
4. Register all three projections as `ready` only after their counts and
   release fingerprints are known.

## Verify and cut over

Run `database/corpus/verify_live_corpus_parity.py` with the exact source,
embedding artifact, and the builder recorded in the release lock. For the
active 2026-08-18 release that builder is commit `1b98f44`; pass it through
`--pipeline-root` from a trusted checkout. The command must report the
requested fingerprint, matching canonical counts, zero ID/content mismatches,
and no invalid embedding artifact. It now fails closed when
`canonical_validation.json` or a rebuilt source has a different fingerprint.
A historical `live_parity.json` is never sufficient.

Only after parity passes, call the guarded release activation function and
verify `/health`, `/ready`, exact lookup, table calculation, citation HTML,
high-risk abstention, and SSE. Keep the old release until rollback evidence is
recorded.
