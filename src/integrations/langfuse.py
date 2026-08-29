from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from src.config import get_settings
from src.integrations.otel import otel_span

logger = logging.getLogger(__name__)

_configured = False
_runtime_available: bool | None = None
_prompt_cache: tuple[str, str, float] | None = None


def tracing_enabled() -> bool:
    # Read-only evals must remain independent of remote telemetry availability.
    # This guard is checked before Settings so a cached pydantic-settings value
    # loaded from a developer .env cannot re-enable Langfuse mid-run.
    if os.getenv("P151_EVAL_DISABLE_REMOTE_TRACING", "").casefold() in {"1", "true", "yes"}:
        return False
    settings = get_settings()
    if settings.app_env == "test":
        return False
    return settings.langfuse_configured


def configure_langfuse() -> None:
    """Copy Settings into process env before the Langfuse client is created."""
    global _configured
    # Keep the transitive LangChain tracer disabled. Langfuse is the only
    # supported telemetry backend for the online runtime.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    for _name in (
        "LANGCHAIN_API_KEY",
        "LANGCHAIN_PROJECT",
        "LANGCHAIN_ENDPOINT",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
    ):
        os.environ.pop(_name, None)
    settings = get_settings()
    public_key = settings.langfuse_public_key.strip()
    secret_key = settings.langfuse_secret_key.strip()
    host = (settings.langfuse_base_url or settings.langfuse_host).strip()
    if not public_key or not secret_key or not host:
        return
    os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    os.environ["LANGFUSE_SECRET_KEY"] = secret_key
    os.environ["LANGFUSE_BASE_URL"] = host
    os.environ["LANGFUSE_HOST"] = host
    # Langfuse's synchronous SDK reads this setting when constructing the
    # client. Keep prompt/trace control-plane calls bounded so a missing
    # registry label cannot stall the async chat path; resolve_prompt remains
    # fail-open and caches the local fallback after the bounded attempt.
    os.environ["LANGFUSE_TIMEOUT"] = str(settings.langfuse_timeout_seconds)
    os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", settings.app_env)
    _configured = True


def get_callback_handler():
    global _runtime_available
    if not tracing_enabled():
        return None
    if _runtime_available is False:
        return None
    try:
        configure_langfuse()
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception:
        _runtime_available = False
        logger.warning("Langfuse callback unavailable; continuing without tracing")
        return None


def llm_invoke_config() -> dict[str, Any]:
    handler = get_callback_handler()
    if handler is None:
        return {}
    return {"callbacks": [handler]}


def resolve_prompt(default_prompt: str) -> tuple[str, str]:
    """Resolve the production prompt from Langfuse with a bounded local cache.

    Prompt retrieval is fail-open for local/offline operation, but the
    returned version is always explicit (registry version or content hash),
    allowing a benchmark and a live trace to be reproduced exactly.
    """
    global _prompt_cache
    settings = get_settings()
    fallback_version = "local:" + __import__("hashlib").sha256(
        default_prompt.encode("utf-8")
    ).hexdigest()[:16]
    now = time.monotonic()
    if _prompt_cache and now - _prompt_cache[2] < settings.prompt_registry_cache_ttl_seconds:
        return _prompt_cache[0], _prompt_cache[1]
    if not tracing_enabled() or not settings.prompt_registry_name.strip():
        _prompt_cache = (default_prompt, fallback_version, now)
        return default_prompt, fallback_version
    try:
        configure_langfuse()
        from langfuse import get_client

        client = get_client()
        try:
            prompt = client.get_prompt(
                settings.prompt_registry_name,
                label=settings.prompt_registry_label or None,
                cache_ttl_seconds=settings.prompt_registry_cache_ttl_seconds,
            )
        except TypeError:
            # Older Langfuse SDKs do not expose cache_ttl_seconds.
            prompt = client.get_prompt(
                settings.prompt_registry_name,
                label=settings.prompt_registry_label or None,
            )
        text = getattr(prompt, "prompt", None)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Langfuse prompt is empty")
        version = getattr(prompt, "version", None)
        version_text = f"langfuse:{settings.prompt_registry_name}:{version}" if version is not None else (
            "langfuse:" + settings.prompt_registry_name
        )
        _prompt_cache = (text, version_text, now)
        return text, version_text
    except Exception:
        # A telemetry/control-plane outage must not make a healthy model
        # unusable.  The local hash still gives operators a precise lineage.
        logger.warning("Langfuse Prompt Registry unavailable; using local prompt", exc_info=True)
        _prompt_cache = (default_prompt, fallback_version, now)
        return default_prompt, fallback_version


def reset_prompt_cache() -> None:
    global _prompt_cache
    _prompt_cache = None


@asynccontextmanager
async def trace_span(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
):
    global _runtime_available
    if not tracing_enabled() or _runtime_available is False:
        with otel_span(name, metadata=metadata) as observation:
            yield observation
        return
    try:
        configure_langfuse()
        from langfuse import get_client

        observation_context = get_client().start_as_current_observation(as_type=as_type, name=name)
    except Exception:
        # Observability must be fail-open: tracing may never turn a healthy
        # retrieval request into a user-visible outage.
        _runtime_available = False
        logger.warning("Langfuse trace unavailable; continuing without tracing")
        with otel_span(name, metadata=metadata) as observation:
            yield observation
        return

    with otel_span(name, metadata=metadata) as otel_observation:
        with observation_context as observation:
            updates: dict[str, Any] = {}
            if input is not None:
                updates["input"] = input
            if metadata:
                updates["metadata"] = dict(metadata)
            if updates:
                observation.update(**updates)
            yield observation if observation is not None else otel_observation


def flush_langfuse() -> None:
    if not _configured and not tracing_enabled():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception:
        logger.debug("Langfuse flush skipped", exc_info=True)
