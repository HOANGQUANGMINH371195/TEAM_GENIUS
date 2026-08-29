-- Keep the reviewed-fact fast path cheap when a release contains a large
-- historical table_cell_facts projection but no accepted rows.
BEGIN;
CREATE INDEX IF NOT EXISTS table_cell_facts_accepted_dataset_idx
    ON public.table_cell_facts(dataset_id)
    WHERE payload ->> 'review_status' = 'accepted';
COMMIT;
