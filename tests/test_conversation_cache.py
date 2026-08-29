import asyncio

from src.services.conversation_cache import ConversationContextCache


def test_cache_is_owner_scoped_and_single_flight():
    async def scenario():
        cache = ConversationContextCache(ttl_seconds=60, max_turns=2)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return [{"turn_id": "1"}, {"turn_id": "2"}, {"turn_id": "3"}]

        rows = await asyncio.gather(*[
            cache.get_or_load(owner_uid="u", conversation_id="c", loader=loader)
            for _ in range(3)
        ])
        assert calls == 1
        assert rows[0] == [{"turn_id": "2"}, {"turn_id": "3"}]
        assert await cache.get(owner_uid="other", conversation_id="c") is None

    asyncio.run(scenario())


def test_cache_is_release_scoped():
    async def scenario():
        cache = ConversationContextCache(ttl_seconds=60, max_turns=2)

        async def loader():
            return [{"turn_id": "release-a"}]

        await cache.get_or_load(
            owner_uid="u", conversation_id="c", release_id="release-a", loader=loader
        )
        assert await cache.get(
            owner_uid="u", conversation_id="c", release_id="release-a"
        ) == [{"turn_id": "release-a"}]
        assert await cache.get(
            owner_uid="u", conversation_id="c", release_id="release-b"
        ) is None

    asyncio.run(scenario())


def test_cache_is_prompt_scoped():
    async def scenario():
        cache = ConversationContextCache(ttl_seconds=60, max_turns=2)

        async def loader():
            return [{"turn_id": "prompt-a"}]

        await cache.get_or_load(
            owner_uid="u", conversation_id="c", release_id="r",
            prompt_version="prompt-a", loader=loader,
        )
        assert await cache.get(
            owner_uid="u", conversation_id="c", release_id="r", prompt_version="prompt-a"
        ) == [{"turn_id": "prompt-a"}]
        assert await cache.get(
            owner_uid="u", conversation_id="c", release_id="r", prompt_version="prompt-b"
        ) is None

    asyncio.run(scenario())


def test_redis_failure_falls_back_to_private_memory_without_data_loss():
    class BrokenRedis:
        async def get(self, _key):
            raise OSError("redis unavailable")

        async def set(self, *_args, **_kwargs):
            raise OSError("redis unavailable")

        async def delete(self, _key):
            raise OSError("redis unavailable")

        async def aclose(self):
            return None

    async def scenario():
        cache = ConversationContextCache(ttl_seconds=60, max_turns=3)
        cache._redis = BrokenRedis()

        async def loader():
            return [{"turn_id": "1"}, {"turn_id": "2"}]

        first = await cache.get_or_load(
            owner_uid="u", conversation_id="c", release_id="r", loader=loader
        )
        second = await cache.get(
            owner_uid="u", conversation_id="c", release_id="r"
        )
        assert first == second == [{"turn_id": "1"}, {"turn_id": "2"}]

    asyncio.run(scenario())


def test_structured_facts_survive_turn_bounding_and_remain_owner_scoped():
    async def scenario():
        cache = ConversationContextCache(ttl_seconds=60, max_turns=2)

        async def loader():
            return [
                {"user_facts": {"emergency": False}},
                {"turn_id": "1"},
                {"turn_id": "2"},
                {"turn_id": "3"},
            ]

        rows = await cache.get_or_load(
            owner_uid="owner-a", conversation_id="conversation", release_id="release", loader=loader
        )
        assert rows == [
            {"user_facts": {"emergency": False}},
            {"turn_id": "2"},
            {"turn_id": "3"},
        ]
        assert await cache.get(
            owner_uid="owner-b", conversation_id="conversation", release_id="release"
        ) is None

    asyncio.run(scenario())
