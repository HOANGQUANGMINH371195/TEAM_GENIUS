import asyncio

import pytest

from src.services.research_jobs import ResearchJobQueue, ResearchQueueFullError


@pytest.mark.asyncio
async def test_research_jobs_complete_and_isolate_owner():
    queue = ResearchJobQueue(max_pending=2, max_workers=1, timeout_seconds=1)

    async def execute(job):
        await asyncio.sleep(0)
        return {"evidence_count": 2, "job_release": job.release_id}

    job = await queue.submit(
        owner_uid="user-a", conversation_id="conversation-a", release_id="snapshot-a",
        query="tổng quan", executor=execute,
    )
    assert await queue.get(owner_uid="user-b", conversation_id="conversation-a", job_id=job.job_id) is None
    await asyncio.wait_for(queue._tasks[job.job_id], timeout=1)
    status = await queue.get(owner_uid="user-a", conversation_id="conversation-a", job_id=job.job_id)
    assert status is not None
    assert status.status == "completed"
    assert status.public_status()["result"] == {"evidence_count": 2, "job_release": "snapshot-a"}
    await queue.close()


@pytest.mark.asyncio
async def test_research_jobs_bound_queue_and_timeout():
    queue = ResearchJobQueue(max_pending=1, max_workers=1, timeout_seconds=0.01)

    async def slow(_job):
        await asyncio.sleep(1)

    first = await queue.submit(
        owner_uid="user", conversation_id="conversation", release_id="snapshot",
        query="deep", executor=slow,
    )
    with pytest.raises(ResearchQueueFullError):
        await queue.submit(
            owner_uid="user", conversation_id="conversation", release_id="snapshot",
            query="second", executor=slow,
        )
    await asyncio.wait_for(queue._tasks[first.job_id], timeout=1)
    status = await queue.get(owner_uid="user", conversation_id="conversation", job_id=first.job_id)
    assert status is not None and status.status == "expired"
    await queue.close()


@pytest.mark.asyncio
async def test_research_jobs_cancel_before_execution():
    queue = ResearchJobQueue(max_pending=2, max_workers=1, timeout_seconds=1)
    blocker = asyncio.Event()

    async def blocked(_job):
        await blocker.wait()
        return {}

    first = await queue.submit(
        owner_uid="user", conversation_id="conversation", release_id="snapshot",
        query="first", executor=blocked,
    )
    second = await queue.submit(
        owner_uid="user", conversation_id="conversation", release_id="snapshot",
        query="second", executor=blocked,
    )
    assert await queue.cancel(owner_uid="user", conversation_id="conversation", job_id=second.job_id)
    assert (await queue.get(owner_uid="user", conversation_id="conversation", job_id=second.job_id)).status == "cancelled"
    blocker.set()
    await asyncio.wait_for(queue._tasks[first.job_id], timeout=1)
    await asyncio.gather(queue._tasks[second.job_id], return_exceptions=True)
    await queue.close()
