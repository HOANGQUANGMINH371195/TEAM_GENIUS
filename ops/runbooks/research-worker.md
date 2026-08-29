# Durable research worker

Deep/global requests may be handed to a separate worker so an interactive SSE
request is never held open. The API must set:

```dotenv
RESEARCH_QUEUE_BACKEND=redis
RESEARCH_QUEUE_REDIS_URL=rediss://:<password>@<redis-host>:<port>/0
```

The worker runs the same image with:

```bash
python -m src.research_worker
```

The `render-research-worker.yaml` blueprint is retained only as a legacy
migration artifact; it is deliberately not included in the default API
blueprint. AWS production runs the worker from the Compose profile on EC2.

The worker refuses to start unless the Redis backend is explicit. Jobs are
owner/conversation/release scoped, bounded by TTL and timeout, and persist only
the public answer/citation envelope. Deploy it as a separately scaled AWS
Compose worker after Redis connectivity, restart recovery, cancellation, and
rollback are demonstrated. The in-process queue remains the development
fallback and is not a production durability claim.
