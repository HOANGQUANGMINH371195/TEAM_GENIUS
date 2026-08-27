import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import document_html


class _SessionScope:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_document_viewer_verifies_hash_sanitizes_html_and_sets_browser_guards():
    raw_html = (
        '<h2 id="dieu-1">Điều 1</h2><a href="#dieu-1">Mục lục</a>'
        '<img src="https://evil.example/x" onerror="alert(1)">'
        '<script>window.location="https://evil.example"</script>'
    )
    document = {
        "raw_html": raw_html,
        "raw_html_sha256": hashlib.sha256(raw_html.encode("utf-8")).hexdigest(),
        "title": "Luật BHYT",
    }
    repository = SimpleNamespace(public_document_html=AsyncMock(return_value=document))
    with (
        patch("src.api.routes.get_settings", return_value=SimpleNamespace(feature_viewer_enabled=True)),
        patch("src.api.routes.session_scope", return_value=_SessionScope()),
        patch("src.api.routes.GraphRepository", return_value=repository),
    ):
        response = await document_html("01/2026/QH15", _user={})

    assert response.status_code == 200
    assert "Điều 1" in response.body.decode("utf-8")
    assert "href=\"#dieu-1\"" in response.body.decode("utf-8")
    assert "<script" not in response.body.decode("utf-8").lower()
    assert "onerror" not in response.body.decode("utf-8").lower()
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"].startswith("public, max-age=300")
    repository.public_document_html.assert_awaited_once_with("01/2026/QH15")


@pytest.mark.asyncio
async def test_document_viewer_rejects_hash_mismatch_before_rendering():
    repository = SimpleNamespace(
        public_document_html=AsyncMock(
            return_value={"raw_html": "<p>canonical</p>", "raw_html_sha256": "0" * 64}
        )
    )
    with (
        patch("src.api.routes.get_settings", return_value=SimpleNamespace(feature_viewer_enabled=True)),
        patch("src.api.routes.session_scope", return_value=_SessionScope()),
        patch("src.api.routes.GraphRepository", return_value=repository),
    ):
        with pytest.raises(HTTPException) as error:
            await document_html("01/2026/QH15", _user={})

    assert error.value.status_code == 503
    assert error.value.detail == "Document integrity check failed"


@pytest.mark.asyncio
async def test_document_viewer_rejects_path_traversal_signature():
    with patch("src.api.routes.get_settings", return_value=SimpleNamespace(feature_viewer_enabled=True)):
        with pytest.raises(HTTPException) as error:
            await document_html("../secrets", _user={})

    assert error.value.status_code == 404
