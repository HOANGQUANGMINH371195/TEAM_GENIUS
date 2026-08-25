BEGIN;

-- Restore the pre-migration policy expressions. The Firebase accessor is
-- intentionally retained: dropping it would break any policy created during
-- a partial deploy; it is harmless and can be removed in a later cleanup.
DROP POLICY IF EXISTS authenticated_read_own ON public.users;
CREATE POLICY authenticated_read_own ON public.users
    FOR SELECT TO authenticated USING (uid = auth.uid()::text);
DROP POLICY IF EXISTS owner_all ON public.conversations;
CREATE POLICY owner_all ON public.conversations
    FOR ALL TO authenticated USING (owner_uid = auth.uid()::text)
    WITH CHECK (owner_uid = auth.uid()::text);
DROP POLICY IF EXISTS owner_all ON public.conversation_turns;
CREATE POLICY owner_all ON public.conversation_turns
    FOR ALL TO authenticated USING (owner_uid = auth.uid()::text)
    WITH CHECK (owner_uid = auth.uid()::text);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.datasets WHERE status = 'verified') THEN
        RAISE EXCEPTION 'cannot rollback verified status while verified releases exist';
    END IF;
END
$$;

ALTER TABLE public.datasets DROP CONSTRAINT IF EXISTS datasets_status_check;
ALTER TABLE public.datasets
    ADD CONSTRAINT datasets_status_check
    CHECK (status IN ('staging', 'active', 'failed', 'superseded'));

COMMIT;
