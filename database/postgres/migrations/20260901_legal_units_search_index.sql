-- Index parsed legal-unit labels/headings for fast operative-clause rescue.
CREATE INDEX IF NOT EXISTS legal_units_search_vector_idx
    ON public.legal_units USING gin
       (to_tsvector('simple', coalesce(label, '') || ' ' || coalesce(heading, '') || ' ' || coalesce(text, '')));
