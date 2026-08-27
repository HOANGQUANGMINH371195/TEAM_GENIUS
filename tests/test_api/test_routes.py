from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.api.auth import get_current_user
from src.api.routes import _public_route, _trace_stage_metrics
from src.main import app
from src.models.graph import RetrievalResult


def test_langfuse_stage_export_is_allowlisted_and_secret_free():
    metrics = _trace_stage_metrics(
        {
            "metadata": {
                "route_intent": "relational",
                "retrieval_ms": 12.3,
                "generation_ms": 4.5,
                "retrieval_trace": {"chunk_ids": ["private"]},
                "OPENAI_API_KEY": "secret",
            }
        }
    )
    assert metrics == {
        "route_intent": "relational",
        "retrieval_ms": 12.3,
        "generation_ms": 4.5,
    }


def test_public_route_exposes_only_known_route_enum():
    assert _public_route({"metadata": {"route_plan": {"route": "temporal"}}}) == "temporal"
    assert _public_route({"metadata": {"route_plan": {"route": "unknown-private-value"}}}) == ""


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_metrics_endpoint_is_prometheus_text(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "medipay_metrics_registry_info" in response.text


@pytest.mark.asyncio
async def test_readiness(client):
    response = await client.get("/ready")
    assert response.status_code in {200, 503}
    assert response.json()["status"] in {"ready", "degraded"}


@pytest.mark.asyncio
async def test_chat_success(client):
    result = {
        "response": "Đã xử lý",
        "citations": [
            RetrievalResult(
                chunk_id="chunk-1",
                document_id="doc-1",
                content="Nguồn pháp lý",
                title="Luật BHYT",
                channels=["semantic"],
            ).model_dump()
        ],
    }
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(return_value=result)
        response = await client.post("/api/v1/chat", json={"message": "Tôi cần hỗ trợ"})

    assert response.status_code == 200
    assert response.json()["response"] == "Đã xử lý"
    assert response.json()["citations"] == [{
        "title": "Luật BHYT",
        "document_number": "",
        "section_title": "",
        "quote": "",
        "source_url": "",
        "source_checked_at": "",
    }]
    serialized = response.text
    assert "chunk-1" not in serialized
    assert "doc-1" not in serialized
    assert "semantic" not in serialized


@pytest.mark.asyncio
async def test_chat_stream_emits_only_verified_final_event(client):
    async def events(*_args, **_kwargs):
        yield {"event": "on_chain_start", "name": "retrieve_vectors", "data": {}}
        yield {
            "event": "on_chain_end",
            "name": "guardrail",
            "data": {
                "output": {
                    "response": "Đã kiểm chứng",
                    "citations": [
                        {
                            "document_id": "private-doc-id",
                            "chunk_id": "private-chunk-id",
                            "dataset_id": "private-dataset-id",
                            "title": "Luật BHYT",
                            "document_number": "01/2026/QH15",
                            "quote": "Nội dung được trích dẫn.",
                            "channels": ["semantic"],
                            "text_sha256": "private-hash",
                        }
                    ],
                    "claims": [{"claim_id": "private-claim-id"}],
                    "metadata": {"route_plan": {"route": "temporal"}},
                }
            },
        }

    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.astream_events = events
        response = await client.post("/api/v1/chat/stream", json={"message": "Câu hỏi"})

    assert response.status_code == 200
    assert "event: status" in response.text
    assert '"response": "Đã kiểm chứng"' in response.text
    assert '"document_number": "01/2026/QH15"' in response.text
    assert '"route": "temporal"' in response.text
    assert "private-doc-id" not in response.text
    assert "private-chunk-id" not in response.text
    assert "private-dataset-id" not in response.text
    assert "private-hash" not in response.text
    assert "private-claim-id" not in response.text
    assert "semantic" not in response.text
    assert "event: done" in response.text


@pytest.mark.asyncio
async def test_chat_requires_authentication(client):
    app.dependency_overrides.pop(get_current_user, None)
    try:
        response = await client.post("/api/v1/chat", json={"message": "Tôi cần hỗ trợ"})
    finally:
        from tests.conftest import _test_user

        app.dependency_overrides[get_current_user] = _test_user

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_rejects_removed_public_history_field(client):
    result = {"response": "Xin chào", "citations": []}
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(return_value=result)
        response = await client.post(
            "/api/v1/chat",
            json={
                "message": "xin chào",
                "chat_history": [{"role": "user", "content": "xin chào"}],
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_empty_agent_response(client):
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(
            return_value={"response": "   ", "citations": []}
        )
        response = await client.post("/api/v1/chat", json={"message": "Xin chào"})

    assert response.status_code == 502
    assert response.json()["code"] == "provider_unavailable"
    assert "empty response" in response.json()["message"]


@pytest.mark.asyncio
async def test_chat_empty_message(client):
    response = await client.post("/api/v1/chat", json={"message": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_chat_message_too_long(client):
    response = await client.post("/api/v1/chat", json={"message": "x" * 5001})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_oversized_http_body_before_parsing(client):
    response = await client.post(
        "/api/v1/chat",
        headers={"content-length": "200000"},
        content=b"{}",
    )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert response.headers["x-request-id"] == response.json()["request_id"]


@pytest.mark.asyncio
async def test_chat_rejects_history_payload_after_migration(client):
    response = await client.post(
        "/api/v1/chat",
        json={
            "message": "question",
            "chat_history": [
                {"role": "user", "content": "x" * 5000},
                {"role": "assistant", "content": "x" * 5000},
                {"role": "user", "content": "x" * 5000},
                {"role": "assistant", "content": "x" * 5000},
                {"role": "user", "content": "x"},
            ],
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_success(client):
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(return_value={"response": "Phân tích hoàn tất"})
        response = await client.post("/api/v1/analyze", json={"message": "Phân tích hóa đơn"})

    assert response.status_code == 200
    assert response.json() == {"analysis": "Phân tích hoàn tất"}


@pytest.mark.asyncio
async def test_analyze_empty_message(client):
    response = await client.post("/api/v1/analyze", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_calculator_scenarios_are_exact_and_bounded(client):
    response = await client.post(
        "/api/v1/calculator/bhyt/scenarios",
        json={
            "scenarios": [
                {
                    "label": "threshold-met",
                    "calculation": {
                        "covered_cost": "1000000.01",
                        "base_rate_percent": "80",
                        "copayment_spend": "6000000",
                        "copayment_threshold": "6000000",
                        "continuous_years": "5",
                        "threshold_rate_percent": "100",
                        "rule_provenance": ["reviewed:table-cell-1"],
                    },
                },
                {
                    "label": "base-rate",
                    "calculation": {
                        "covered_cost": "100",
                        "base_rate_percent": "80",
                    },
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["calculation"] == {
        "covered_cost": "1000000.01",
        "applied_rate_percent": "100.00",
        "insurer_pays": "1000000.01",
        "patient_pays": "0.00",
        "threshold_met": True,
        "formula_id": "bhyt.covered_cost.v1",
        "provenance": ["reviewed:table-cell-1"],
    }
    assert response.json()["results"][1]["calculation"]["insurer_pays"] == "80.00"


@pytest.mark.asyncio
async def test_calculator_scenarios_reject_more_than_eight_cases(client):
    scenarios = [
        {"label": str(index), "calculation": {"covered_cost": "1", "base_rate_percent": "80"}}
        for index in range(9)
    ]
    response = await client.post("/api/v1/calculator/bhyt/scenarios", json={"scenarios": scenarios})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_agent_failure_does_not_expose_internal_error(client):
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(side_effect=ValueError("secret provider failure"))
        response = await client.post("/api/v1/chat", json={"message": "Xin chào"})

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_conversation_history_projects_internal_audit_fields(client):
    row = {
        "turn_id": "550e8400-e29b-41d4-a716-446655440001",
        "user_message": "Quyền lợi là gì?",
        "assistant_response": "Quyền lợi được quy định trong Luật BHYT.",
        "citations": [
            {
                "document_id": "private-doc-id",
                "chunk_id": "private-chunk-id",
                "dataset_id": "private-dataset-id",
                "text_sha256": "private-hash",
                "channels": ["semantic", "legal_graph"],
                "title": "Luật bảo hiểm y tế",
                "document_number": "25/2008/QH12",
                "section_title": "Điều 22",
                "quote": "Quy định về mức hưởng bảo hiểm y tế.",
                "source_url": "https://vbpl.vn/example",
            }
        ],
        "claims": [{"claim_id": "private-claim-id"}],
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    with patch("src.api.auth_routes.get_conversation_store") as store_factory:
        store_factory.return_value.recent_turns = AsyncMock(return_value=[row])
        response = await client.get(
            "/api/v1/auth/conversations/550e8400-e29b-41d4-a716-446655440000/turns"
        )

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["citations"][0]["document_number"] == "25/2008/QH12"
    serialized = response.text
    for private_value in (
        "private-doc-id",
        "private-chunk-id",
        "private-dataset-id",
        "private-hash",
        "private-claim-id",
        "semantic",
        "legal_graph",
    ):
        assert private_value not in serialized


@pytest.mark.asyncio
async def test_swagger_and_openapi(client):
    docs_response = await client.get("/docs")
    openapi_response = await client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    paths = openapi_response.json()["paths"]
    assert {"/health", "/ready", "/api/v1/status", "/api/v1/chat", "/api/v1/chat/stream", "/api/v1/analyze"} <= paths.keys()
    assert paths["/api/v1/chat"]["post"]["requestBody"]
    assert paths["/api/v1/chat"]["post"]["responses"]["200"]
    schemas = openapi_response.json()["components"]["schemas"]
    public_properties = {
        model: set(schemas[model].get("properties", {}))
        for model in ("ChatCitation", "ChatResponse", "ConversationTurn")
    }
    forbidden = {
        "document_id", "chunk_id", "dataset_id", "text_sha256", "channels", "claims"
    }
    assert all(not (properties & forbidden) for properties in public_properties.values())
