# Cache runbook

Redis keys are namespaced under `medipay:` and conversation keys are hashed
from `owner_uid` and `conversation_id`; raw history is never shared between
users. Cache values are navigation hints only. A legal request always
retrieves evidence from the active release again.

To disable Redis safely, unset `RATE_LIMIT_REDIS_URL`; the bounded in-process
fallback remains available for one replica. To invalidate after a release or
schema change, rotate the release deployment or delete only the
`medipay:conversation-context:*` namespace. Never flush a shared Redis
instance without confirming the database owner and other services.

Monitor hit/miss ratio, stale reads, serialization failures and memory
evictions. A cache failure must degrade to PostgreSQL, not to an answer from
stale evidence.
