from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from src.config import get_settings


class LlmConfigurationError(RuntimeError):
    """The configured provider cannot serve chat requests."""


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    settings = get_settings()
    if settings.llm_provider.casefold() != "openai":
        raise LlmConfigurationError("Only OpenAI is supported for chat")
    if not settings.openai_api_key or not settings.model_name:
        raise LlmConfigurationError("OpenAI chat provider is not configured")
    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.openai_api_key,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_output_tokens,
        max_retries=2,
    )


def close_llm() -> None:
    """Drop the process-wide model wrapper during application shutdown/tests."""
    get_llm.cache_clear()
