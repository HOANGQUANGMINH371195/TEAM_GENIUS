from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness(client):
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] in {"ready", "degraded"}


@pytest.mark.asyncio
async def test_chat_success(client):
    with patch("src.api.routes.agent.ainvoke", new_callable=AsyncMock) as invoke:
        invoke.return_value = {"response": "Đã xử lý", "analysis": "Đã phân tích"}
        response = await client.post("/api/v1/chat", json={"message": "Tôi cần hỗ trợ"})

    assert response.status_code == 200
    assert response.json() == {"response": "Đã xử lý", "analysis": "Đã phân tích"}
    invoke.assert_awaited_once_with({"query": "Tôi cần hỗ trợ"})


@pytest.mark.asyncio
async def test_chat_empty_message(client):
    response = await client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_message_too_long(client):
    response = await client.post("/api/v1/chat", json={"message": "x" * 5001})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_success(client):
    with patch("src.api.routes.agent.ainvoke", new_callable=AsyncMock) as invoke:
        invoke.return_value = {"analysis": "Phân tích hoàn tất", "response": ""}
        response = await client.post("/api/v1/analyze", json={"message": "Phân tích hóa đơn"})

    assert response.status_code == 200
    assert response.json() == {"analysis": "Phân tích hoàn tất"}
    invoke.assert_awaited_once_with({"query": "Phân tích hóa đơn"})


@pytest.mark.asyncio
async def test_analyze_empty_message(client):
    response = await client.post("/api/v1/analyze", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "agent": "LangGraph Agent v1.0"}


@pytest.mark.asyncio
async def test_agent_failure_does_not_expose_internal_error(client):
    with patch("src.api.routes.agent.ainvoke", new_callable=AsyncMock) as invoke:
        invoke.side_effect = ValueError("secret provider failure")
        response = await client.post("/api/v1/chat", json={"message": "Xin chào"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal agent error"}


@pytest.mark.asyncio
async def test_swagger_and_openapi(client):
    docs_response = await client.get("/docs")
    openapi_response = await client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200

    paths = openapi_response.json()["paths"]
    assert {"/health", "/ready", "/api/v1/status", "/api/v1/chat", "/api/v1/analyze"} <= paths.keys()
    assert paths["/api/v1/chat"]["post"]["requestBody"]
    assert paths["/api/v1/analyze"]["post"]["responses"]["200"]
