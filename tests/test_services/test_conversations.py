from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import ProgrammingError

from src.services.conversations import (
    ConversationStore,
    ConversationStoreError,
    _json,
    _uuid,
)


def test_conversation_ids_are_canonicalized() -> None:
    assert _uuid("550e8400-e29b-41d4-a716-446655440000", "conversation_id") == "550e8400-e29b-41d4-a716-446655440000"


def test_conversation_ids_reject_untrusted_arbitrary_strings() -> None:
    with pytest.raises(ConversationStoreError):
        _uuid("../../users", "conversation_id")


def test_conversation_payload_is_compact_and_json_safe() -> None:
    assert _json([{"text": "Mức hưởng", "value": 80}]) == '[{"text":"Mức hưởng","value":80}]'


class _Rows:
    def mappings(self):
        return []


class _SessionScope:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_recent_turns_preserves_legacy_context_before_facts_migration() -> None:
    session = AsyncMock()
    session.execute.side_effect = [
        ProgrammingError("SELECT facts", {}, Exception("column facts does not exist")),
        _Rows(),
    ]
    with patch("src.services.conversations.session_scope", return_value=_SessionScope(session)):
        rows = await ConversationStore().recent_turns(
            owner_uid="owner",
            conversation_id="550e8400-e29b-41d4-a716-446655440000",
        )

    assert rows == []
    session.rollback.assert_awaited_once()
