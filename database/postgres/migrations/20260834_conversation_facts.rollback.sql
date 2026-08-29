BEGIN;

ALTER TABLE public.conversations
    DROP CONSTRAINT IF EXISTS conversations_facts_object;
ALTER TABLE public.conversations
    DROP COLUMN IF EXISTS facts;

COMMIT;
