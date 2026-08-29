BEGIN;

DROP TRIGGER IF EXISTS vbpl_ingest_stages_updated_at ON public.vbpl_ingest_stages;
DROP TRIGGER IF EXISTS vbpl_ingest_items_updated_at ON public.vbpl_ingest_items;
DROP TRIGGER IF EXISTS vbpl_ingest_jobs_updated_at ON public.vbpl_ingest_jobs;
DROP FUNCTION IF EXISTS public.update_vbpl_job_updated_at();
DROP TABLE IF EXISTS public.vbpl_ingest_stages;
DROP TABLE IF EXISTS public.vbpl_ingest_items;
DROP TABLE IF EXISTS public.vbpl_ingest_jobs;

COMMIT;
