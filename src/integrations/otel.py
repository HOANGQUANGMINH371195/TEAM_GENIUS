"""Optional OpenTelemetry tracing with a bounded, fail-open exporter.

The online path must stay healthy when the collector is absent or unavailable.
Only low-cardinality, allow-listed attributes are exported; request text,
identifiers and evidence are deliberately excluded.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from src.config import get_settings

logger = logging.getLogger(__name__)
_configured = False
_tracer = None
_provider = None

_ALLOWED_ATTRIBUTES = {
    "stage",
    "route",
    "outcome",
    "model_version",
    "model",
    "prompt_version",
    "release_id",
    "dependency",
    "fallback",
    "feature",
    "http_method",
    "http_route",
    "http_status_code",
}


def _headers(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip() and value.strip():
            parsed[key.strip()] = value.strip()
    return parsed


def configure_otel() -> bool:
    """Configure a single OTLP HTTP batch exporter when explicitly enabled."""

    global _configured, _provider, _tracer
    if _configured:
        return _tracer is not None
    _configured = True
    settings = get_settings()
    if settings.app_env == "test":
        return False
    endpoint = (
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        or settings.otel_exporter_otlp_endpoint.strip()
    )
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        resource = Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": "1.0.0",
                "deployment.environment.name": settings.app_env,
            }
        )
        provider = TracerProvider(
            resource=resource,
            sampler=TraceIdRatioBased(settings.otel_sampling_ratio),
        )
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers=_headers(
                os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
                or settings.otel_exporter_otlp_headers
            ),
            timeout=settings.otel_export_timeout_seconds,
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=settings.otel_max_queue_size,
                max_export_batch_size=settings.otel_max_export_batch_size,
                schedule_delay_millis=settings.otel_schedule_delay_millis,
            )
        )
        # Do not replace a provider installed by an embedding application. In
        # that case use its tracer and retain the host application's exporter.
        current = trace.get_tracer_provider()
        if current.__class__.__name__ == "ProxyTracerProvider":
            trace.set_tracer_provider(provider)
            _provider = provider
        else:
            _provider = current
        _tracer = trace.get_tracer("medipay", "1.0.0")
        return True
    except Exception:
        logger.warning("OpenTelemetry unavailable; continuing without OTLP tracing", exc_info=True)
        _tracer = None
        return False


def _attributes(metadata: Mapping[str, Any] | None) -> dict[str, str | int | float | bool]:
    attrs: dict[str, str | int | float | bool] = {}
    for key, value in (metadata or {}).items():
        if key not in _ALLOWED_ATTRIBUTES or not isinstance(value, (str, int, float, bool)):
            continue
        if isinstance(value, str) and len(value) > 256:
            value = value[:256]
        attrs[f"medipay.{key}"] = value
    return attrs


@contextmanager
def otel_span(
    name: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Yield a bounded OTel span, or ``None`` when telemetry is disabled."""

    if _tracer is None:
        configure_otel()
    if _tracer is None:
        yield None
        return
    try:
        attrs = _attributes(metadata)
        attrs.setdefault("medipay.stage", name)
        with _tracer.start_as_current_span(name, attributes=attrs) as span:
            try:
                yield span
            except Exception as exc:
                try:
                    from opentelemetry.trace import Status, StatusCode

                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                except Exception:
                    logger.debug("Unable to annotate OTel error", exc_info=True)
                raise
            else:
                try:
                    from opentelemetry.trace import Status, StatusCode

                    span.set_status(Status(StatusCode.OK))
                except Exception:
                    logger.debug("Unable to annotate OTel success", exc_info=True)
    except Exception:
        # Exporter/context errors must never alter the answer path.
        logger.warning("OpenTelemetry span failed; continuing without telemetry", exc_info=True)
        yield None


def shutdown_otel() -> None:
    global _configured, _provider, _tracer
    provider = _provider
    if provider is not None:
        try:
            provider.force_flush(timeout_millis=2_000)
            provider.shutdown()
        except Exception:
            logger.debug("OpenTelemetry shutdown skipped", exc_info=True)
    _provider = None
    _tracer = None
    _configured = False


def reset_otel() -> None:
    """Reset process globals for tests and controlled worker reloads."""

    shutdown_otel()
