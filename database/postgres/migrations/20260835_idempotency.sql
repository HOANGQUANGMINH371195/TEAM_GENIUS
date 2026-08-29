-- Idempotency records for chat/mutation retries.
-- Payloads contain only the request hash and bounded public response; prompts,
-- evidence and credentials are never persisted here.
BEGIN;

CREATE TABLE IF NOT EXISTS public.idempotency_records (
    owner_uid text NOT NULL REFERENCES public.users(uid) ON DELETE CASCADE,
    endpoint text NOT NULL,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('processing', 'completed')),
    request_id text NOT NULL DEFAULT '',
    response jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours'),
    PRIMARY KEY (owner_uid, endpoint, idempotency_key),
    CONSTRAINT idempotency_key_length CHECK (char_length(idempotency_key) BETWEEN 8 AND 128),
    CONSTRAINT idempotency_hash_length CHECK (char_length(request_hash) = 64),
    CONSTRAINT idempotency_completed_response CHECK (status = 'processing' OR response IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idempotency_expiry_idx
    ON public.idempotency_records (expires_at);

COMMIT;
