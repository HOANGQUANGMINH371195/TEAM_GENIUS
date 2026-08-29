import json

import pytest

from src.services.vbpl_session import VbplSessionManager


@pytest.mark.asyncio
async def test_detail_normalizes_reference_fields(monkeypatch):
    payload = {
        "data": {
            "id": "doc-1",
            "documentContent": {"content": "<p>text</p>"},
            "references": [{
                "id": "ref-1",
                "referenceType": {"kind": "new"},
                "referenceProvisions": ["Điều 1"],
                "targetDocument": {
                    "id": "target-1",
                    "docNum": "01/QĐ",
                    "title": "Target",
                },
            }],
        }
    }

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(
        "src.services.vbpl_session.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )
    result = await VbplSessionManager.fetch_document_detail("doc-1")
    reference = result["references"][0]
    assert reference["reference_id"] == "ref-1"
    assert reference["reference_type"] == {"kind": "new"}
    assert reference["reference_type_json"] == json.dumps(
        {"kind": "new"}, separators=(",", ":")
    )
    assert reference["reference_provisions"] == ["Điều 1"]
    assert reference["target_id"] == "target-1"
