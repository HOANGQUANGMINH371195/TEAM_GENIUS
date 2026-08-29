from __future__ import annotations

from src.config import Settings


def test_production_contract_rejects_missing_managed_secrets(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    settings = Settings(app_env="production")
    try:
        settings.validate_production_contract()
    except ValueError as exc:
        assert "Production configuration incomplete" in str(exc)
        assert "FIREBASE_SERVICE_ACCOUNT_JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected production contract failure")


def test_production_contract_rejects_malformed_firebase_json(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    settings = Settings(app_env="production", firebase_service_account_json="not-json")
    try:
        settings.validate_production_contract()
    except ValueError as exc:
        assert "valid service-account JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected malformed Firebase credential failure")


def test_non_production_contract_allows_local_defaults():
    Settings(app_env="test").validate_production_contract()


def test_production_contract_requires_durable_global_search_dependencies():
    settings = Settings(
        app_env="production",
        feature_global_search_enabled=True,
        community_index_path="",
        research_queue_backend="memory",
        research_queue_redis_url="",
    )
    try:
        settings.validate_production_contract()
    except ValueError as exc:
        message = str(exc)
        assert "COMMUNITY_INDEX_PATH" in message
        assert "RESEARCH_QUEUE_BACKEND=redis" in message
        assert "RESEARCH_QUEUE_REDIS_URL" in message
    else:  # pragma: no cover
        raise AssertionError("expected global-search production contract failure")


def test_production_contract_requires_experience_index_when_enabled():
    settings = Settings(app_env="production", feature_experience_retrieval_enabled=True)
    try:
        settings.validate_production_contract()
    except ValueError as exc:
        assert "EXPERIENCE_INDEX_PATH" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected experience-index production contract failure")
