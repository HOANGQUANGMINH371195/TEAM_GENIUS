from src.config import get_settings
from src.integrations.langfuse import reset_prompt_cache, resolve_prompt, tracing_enabled


def test_tracing_disabled_in_test_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    get_settings.cache_clear()
    try:
        assert tracing_enabled() is False
    finally:
        get_settings.cache_clear()


def test_langfuse_configured_when_keys_present(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    get_settings.cache_clear()
    try:
        assert get_settings().langfuse_configured is True
        assert tracing_enabled() is True
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()


def test_prompt_resolver_has_reproducible_offline_lineage(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    reset_prompt_cache()
    try:
        prompt, version = resolve_prompt("fallback prompt")
        assert prompt == "fallback prompt"
        assert version.startswith("local:")
        prompt_again, version_again = resolve_prompt("different fallback")
        assert (prompt_again, version_again) == (prompt, version)
    finally:
        reset_prompt_cache()
        get_settings.cache_clear()
