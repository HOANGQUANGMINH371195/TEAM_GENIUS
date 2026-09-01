-- Speed up reviewed table-fact lookup without scanning the full projection.
CREATE INDEX IF NOT EXISTS table_cell_facts_search_vector_idx
    ON public.table_cell_facts USING gin
       (to_tsvector('simple', subject || ' ' || attribute || ' ' || value))
    WHERE payload ->> 'review_status' = 'accepted';
