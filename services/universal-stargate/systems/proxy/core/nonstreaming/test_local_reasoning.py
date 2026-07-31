"""Unit tests for local reasoning → enable_thinking mapping."""

from systems.proxy.core.nonstreaming.local_reasoning import (
    apply_local_reasoning_off,
    extract_reasoning_effort,
    is_local_model_id,
)


def test_is_local_model_id() -> None:
    assert is_local_model_id("hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid")
    assert not is_local_model_id("openrouter/writer/palmyra-x5")
    assert not is_local_model_id("openai/gpt-oss-120b")


def test_extract_effort_from_reasoning_object() -> None:
    assert (
        extract_reasoning_effort({"reasoning": {"effort": "none"}}) == "none"
    )
    assert extract_reasoning_effort({"reasoning_effort": "Minimal"}) == "minimal"
    assert extract_reasoning_effort({}) is None


def test_apply_maps_none_to_enable_thinking_false() -> None:
    body: dict = {"reasoning": {"effort": "none"}, "messages": []}
    assert apply_local_reasoning_off(
        body, model_id="hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
    )
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert body["reasoning"] == {"effort": "none"}  # left intact


def test_apply_skips_cloud_models() -> None:
    body: dict = {"reasoning": {"effort": "none"}}
    assert not apply_local_reasoning_off(
        body, model_id="openrouter/nousresearch/hermes-4-70b"
    )
    assert "chat_template_kwargs" not in body


def test_apply_overrides_profile_thinking_on() -> None:
    body: dict = {
        "reasoning": {"effort": "none"},
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert apply_local_reasoning_off(body, model_id="qwen3-5-9b-q8-0-131072")
    assert body["chat_template_kwargs"]["enable_thinking"] is False


def test_apply_noop_when_already_false() -> None:
    body: dict = {
        "reasoning_effort": "none",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert not apply_local_reasoning_off(body, model_id="qwen3-5-9b-q8-0-131072")
