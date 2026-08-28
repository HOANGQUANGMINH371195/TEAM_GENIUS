-- Owner-scoped structured user facts collected by the eligibility checklist.
-- They are navigation context only; current law is always re-retrieved.
BEGIN;

ALTER TABLE public.conversations
    ADD COLUMN IF NOT EXISTS facts jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.conversations
    DROP CONSTRAINT IF EXISTS conversations_facts_object;
ALTER TABLE public.conversations
    ADD CONSTRAINT conversations_facts_object
    CHECK (jsonb_typeof(facts) = 'object');

COMMIT;
