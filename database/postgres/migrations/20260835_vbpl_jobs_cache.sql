BEGIN;

-- Durable state for resumable admin-triggered VBPL imports.
-- The short-lived discovery list stays in Redis; PostgreSQL stores only
-- operational job state needed for polling and retry after process restarts.

CREATE TABLE IF NOT EXISTS public.vbpl_ingest_jobs (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id text NOT NULL REFERENCES public.datasets(dataset_id),
    idempotency_key text NOT NULL UNIQUE,
    requested_by text NOT NULL DEFAULT '',
    trigger text NOT NULL DEFAULT 'manual'
        CHECK (trigger IN ('manual', 'daily', 'retry')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed')),
    lease_until timestamptz,
    heartbeat_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    total_items integer NOT NULL DEFAULT 0 CHECK (total_items >= 0),
    succeeded_items integer NOT NULL DEFAULT 0 CHECK (succeeded_items >= 0),
    failed_items integer NOT NULL DEFAULT 0 CHECK (failed_items >= 0),
    error_message text NOT NULL DEFAULT '',
    request_id text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vbpl_ingest_jobs_claim_idx
    ON public.vbpl_ingest_jobs (status, lease_until, created_at);

CREATE TABLE IF NOT EXISTS public.vbpl_ingest_items (
    job_id uuid NOT NULL REFERENCES public.vbpl_ingest_jobs(job_id) ON DELETE CASCADE,
    doc_id text NOT NULL,
    content_sha256 text NOT NULL DEFAULT '',
    detail_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    current_stage text NOT NULL DEFAULT 'database'
        CHECK (current_stage IN ('database', 'embedding', 'relationships')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed')),
    chunks_count integer NOT NULL DEFAULT 0 CHECK (chunks_count >= 0),
    error_message text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, doc_id)
);
CREATE INDEX IF NOT EXISTS vbpl_ingest_items_status_idx
    ON public.vbpl_ingest_items (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.vbpl_ingest_stages (
    job_id uuid NOT NULL,
    doc_id text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('database', 'embedding', 'relationships')),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_token uuid,
    started_at timestamptz,
    finished_at timestamptz,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text NOT NULL DEFAULT '',
    error_message text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, doc_id, stage),
    FOREIGN KEY (job_id, doc_id)
        REFERENCES public.vbpl_ingest_items(job_id, doc_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS vbpl_ingest_stages_claim_idx
    ON public.vbpl_ingest_stages (status, updated_at DESC);

CREATE OR REPLACE FUNCTION public.update_vbpl_job_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS vbpl_ingest_jobs_updated_at ON public.vbpl_ingest_jobs;
CREATE TRIGGER vbpl_ingest_jobs_updated_at
BEFORE UPDATE ON public.vbpl_ingest_jobs
FOR EACH ROW EXECUTE FUNCTION public.update_vbpl_job_updated_at();
DROP TRIGGER IF EXISTS vbpl_ingest_items_updated_at ON public.vbpl_ingest_items;
CREATE TRIGGER vbpl_ingest_items_updated_at
BEFORE UPDATE ON public.vbpl_ingest_items
FOR EACH ROW EXECUTE FUNCTION public.update_vbpl_job_updated_at();
DROP TRIGGER IF EXISTS vbpl_ingest_stages_updated_at ON public.vbpl_ingest_stages;
CREATE TRIGGER vbpl_ingest_stages_updated_at
BEFORE UPDATE ON public.vbpl_ingest_stages
FOR EACH ROW EXECUTE FUNCTION public.update_vbpl_job_updated_at();

COMMIT;
