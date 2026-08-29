from src.config import get_settings
from src.integrations.otel import _attributes, otel_span, reset_otel


def test_otel_attributes_are_allowlisted_and_bounded():
    attrs = _attributes(
        {
            "route": "simple",
            "release_id": "snapshot-abc",
            "query": "private text must not be exported",
            "prompt_version": "x" * 400,
        }
    )
    assert attrs["medipay.route"] == "simple"
    assert attrs["medipay.release_id"] == "snapshot-abc"
    assert "medipay.query" not in attrs
    assert len(attrs["medipay.prompt_version"]) == 256


def test_otel_is_fail_open_when_disabled_in_tests(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.invalid/v1/traces")
    get_settings.cache_clear()
    reset_otel()
    try:
        with otel_span("unit-test", metadata={"stage": "test"}) as span:
            assert span is None
    finally:
        reset_otel()
        get_settings.cache_clear()
