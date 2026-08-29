import asyncio

import pytest

from src.services.research_jobs import RedisResearchJobQueue, ResearchQueueFullError


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}
        self.sets = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def sadd(self, key, value):
        values = self.sets.setdefault(key, set())
        before = len(values)
        values.add(value)
        return int(len(values) > before)

    async def scard(self, key):
        return len(self.sets.get(key, set()))

    async def srem(self, key, value):
        self.sets.get(key, set()).discard(value)

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def blpop(self, key, timeout=0):
        values = self.lists.get(key, [])
        return (key, values.pop(0)) if values else None


@pytest.mark.asyncio
async def test_redis_queue_persists_owner_scope_and_runs_once():
    redis = FakeRedis()
    queue = RedisResearchJobQueue(redis_client=redis, max_pending=2)
    job = await queue.submit(owner_uid="u1", conversation_id="c1", release_id="snapshot-x", query=" q ")
    assert await queue.get(owner_uid="u2", conversation_id="c1", job_id=job.job_id) is None
    finished = await queue.run_once(lambda item: asyncio.sleep(0, result={"ok": True}))
    assert finished is not None and finished.status == "completed"
    current = await queue.get(owner_uid="u1", conversation_id="c1", job_id=job.job_id)
    assert current is not None and current.result == {"ok": True}


@pytest.mark.asyncio
async def test_redis_queue_bounds_active_jobs():
    queue = RedisResearchJobQueue(redis_client=FakeRedis(), max_pending=1)
    await queue.submit(owner_uid="u", conversation_id="c", release_id="snapshot-x", query="q1")
    with pytest.raises(ResearchQueueFullError):
        await queue.submit(owner_uid="u", conversation_id="c", release_id="snapshot-x", query="q2")
