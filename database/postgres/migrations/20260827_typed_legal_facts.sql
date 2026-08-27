-- Reviewed, release-scoped fact projection. Canonical text remains documents /
-- legal_units; facts are usable only after review and source-hash validation.
BEGIN;

CREATE TABLE IF NOT EXISTS public.legal_facts (
    fact_id text PRIMARY KEY,
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id) ON DELETE CASCADE,
    subject text NOT NULL,
    predicate text NOT NULL,
    normalized_value text NOT NULL,
    effective_from date,
    effective_to date,
    jurisdiction text NOT NULL DEFAULT '',
    provision_id text NOT NULL DEFAULT '',
    document_id text NOT NULL,
    unit_id text NOT NULL,
    source_start integer,
    source_end integer,
    source_sha256 text NOT NULL,
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'accepted', 'rejected')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (dataset_id, document_id) REFERENCES public.documents(dataset_id, id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_id, unit_id) REFERENCES public.legal_units(dataset_id, unit_id) ON DELETE CASCADE,
    CHECK (source_end IS NULL OR source_start IS NULL OR source_end >= source_start),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);

CREATE INDEX IF NOT EXISTS legal_facts_lookup_idx
    ON public.legal_facts(dataset_id, subject, predicate, review_status);
CREATE INDEX IF NOT EXISTS legal_facts_temporal_idx
    ON public.legal_facts(dataset_id, effective_from, effective_to);
ALTER TABLE public.legal_facts ENABLE ROW LEVEL SECURITY;

COMMIT;
