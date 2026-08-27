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
