# AI contract — Nginx

Nginx terminates TLS and proxies web/API/SSE only. It must not cache private
answers, inspect prompts, or bypass Firebase/auth/idempotency checks. Public
document caching and rate limiting remain bounded and release-safe.
