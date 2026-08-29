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
    options = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "timeout": settings.llm_timeout_seconds,
        "max_tokens": settings.llm_max_output_tokens,
        "max_retries": 2,
    }
    if settings.model_name.casefold().startswith("gpt-5"):
        options.update(
            use_responses_api=settings.llm_use_responses_api,
            reasoning_effort=settings.llm_reasoning_effort,
            verbosity=settings.llm_verbosity,
        )
    else:
        options["temperature"] = settings.llm_temperature
    return ChatOpenAI(**options)


@lru_cache(maxsize=1)
def get_rewrite_llm() -> ChatOpenAI:
    """Return the low-latency model profile used only for retrieval rewriting."""
    settings = get_settings()
    if settings.llm_provider.casefold() != "openai":
        raise LlmConfigurationError("Only OpenAI is supported for query rewriting")
    if not settings.openai_api_key or not settings.model_name:
        raise LlmConfigurationError("OpenAI query rewrite provider is not configured")
    options = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "timeout": min(settings.llm_timeout_seconds, 15.0),
        "max_tokens": settings.query_rewrite_max_tokens,
        "max_retries": 1,
    }
    if settings.model_name.casefold().startswith("gpt-5"):
        options.update(
            use_responses_api=settings.llm_use_responses_api,
            reasoning_effort="none",
            verbosity="low",
        )
    else:
        options["temperature"] = 0.0
    return ChatOpenAI(**options)


@lru_cache(maxsize=1)
def get_router_llm() -> ChatOpenAI:
    """Low-output classifier profile; never used to synthesize an answer."""
    settings = get_settings()
    if settings.llm_provider.casefold() != "openai":
        raise LlmConfigurationError("Only OpenAI is supported for request routing")
    if not settings.openai_api_key or not settings.model_name:
        raise LlmConfigurationError("OpenAI router provider is not configured")
    options = {
        "model": settings.model_name,
        "api_key": settings.openai_api_key,
        "timeout": settings.model_router_timeout_seconds,
        "max_tokens": settings.model_router_max_tokens,
        "max_retries": 0,
    }
    if settings.model_name.casefold().startswith("gpt-5"):
        options.update(use_responses_api=settings.llm_use_responses_api, reasoning_effort="none", verbosity="low")
    else:
        options["temperature"] = 0.0
    return ChatOpenAI(**options)


def close_llm() -> None:
    """Drop the process-wide model wrapper during application shutdown/tests."""
    get_llm.cache_clear()
    get_rewrite_llm.cache_clear()
    get_router_llm.cache_clear()
