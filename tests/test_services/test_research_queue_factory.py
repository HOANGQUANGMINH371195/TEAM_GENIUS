import pytest

from src.config import Settings
from src.services.research_jobs import ResearchJobQueue, create_research_queue


def test_factory_defaults_to_bounded_memory_queue():
    queue = create_research_queue(settings=Settings())
    assert isinstance(queue, ResearchJobQueue)


def test_factory_refuses_redis_without_durable_url():
    with pytest.raises(ValueError, match="REDIS_URL"):
        create_research_queue(settings=Settings(research_queue_backend="redis"))
