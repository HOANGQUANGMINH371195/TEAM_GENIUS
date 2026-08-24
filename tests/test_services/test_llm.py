from types import SimpleNamespace
from unittest.mock import patch

from src.services.llm import close_llm, get_llm, get_rewrite_llm


def _settings(model: str = "gpt-5.6-luna") -> SimpleNamespace:
    return SimpleNamespace(
        llm_provider="openai",
        openai_api_key="test-key",
        model_name=model,
        llm_timeout_seconds=45,
        llm_max_output_tokens=900,
        llm_temperature=0.2,
        llm_use_responses_api=True,
        llm_reasoning_effort="medium",
        llm_verbosity="low",
        query_rewrite_max_tokens=180,
    )


def test_gpt5_main_model_uses_responses_reasoning_without_temperature():
    close_llm()
    with (
        patch("src.services.llm.get_settings", return_value=_settings()),
        patch("src.services.llm.ChatOpenAI") as constructor,
    ):
        get_llm()

    options = constructor.call_args.kwargs
    assert options["model"] == "gpt-5.6-luna"
    assert options["use_responses_api"] is True
    assert options["reasoning_effort"] == "medium"
    assert options["verbosity"] == "low"
    assert "temperature" not in options
    close_llm()


def test_rewrite_profile_uses_no_reasoning_and_tight_budget():
    close_llm()
    with (
        patch("src.services.llm.get_settings", return_value=_settings()),
        patch("src.services.llm.ChatOpenAI") as constructor,
    ):
        get_rewrite_llm()

    options = constructor.call_args.kwargs
    assert options["reasoning_effort"] == "none"
    assert options["verbosity"] == "low"
    assert options["max_tokens"] == 180
    assert options["timeout"] == 15
    assert "temperature" not in options
    close_llm()
