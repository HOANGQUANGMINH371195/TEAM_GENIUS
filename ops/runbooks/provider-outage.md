# Provider outage runbook

1. Check `/health`, `/ready` and `/metrics` for provider circuit state.
2. Disable the affected feature flag (`FEATURE_GRAPH_ENABLED` or
   `FEATURE_RERANKER_ENABLED`) using the deployment environment and restart
   only the API revision.
3. Keep PostgreSQL canonical retrieval available; never substitute graph
   labels, cached answers or an unverified model response as legal evidence.
4. If the primary route cannot produce verified evidence, return the bounded
   clarification/abstention response and preserve the previous release.
5. Re-enable the flag only after a paired outage-recovery check and release
   parity check pass. Record request IDs, release ID and provider error class;
   do not record secrets or raw user messages.

## Deep/global research jobs

Global and deep routes are opt-in and bounded. `ResearchJobQueue` enforces
owner/conversation isolation, a worker semaphore, a hard deadline and graceful
shutdown; a timeout or provider outage produces an explicit failed/expired job
and never falls back to unverified summary text. The current implementation is
process-local for development and staging. Production must place the same job
contract behind a durable queue/worker (with retry idempotency and a persisted
release ID) before enabling `FEATURE_GLOBAL_SEARCH_ENABLED` for deep research.
