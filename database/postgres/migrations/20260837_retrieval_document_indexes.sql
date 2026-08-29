-- Retrieval latency: narrow document-bounded operative scans before applying
-- source text matching. This migration is additive and release-independent.
BEGIN;
CREATE INDEX IF NOT EXISTS dataset_legal_units_document_idx
    on public.legal_units (dataset_id, document_id, source_start, unit_id);

COMMIT;
