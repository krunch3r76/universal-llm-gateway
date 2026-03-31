from provider_model_limits import (
    anthropic_max_output_tokens,
    clamp_anthropic_max_tokens,
)


def test_anthropic_max_output_tokens_current_models() -> None:
    assert anthropic_max_output_tokens("claude-opus-4-6") == 128000
    assert anthropic_max_output_tokens("claude-sonnet-4-20250514") == 64000
    assert anthropic_max_output_tokens("claude-haiku-4-5-20251001") == 64000


def test_anthropic_max_output_tokens_internal_aliases() -> None:
    assert anthropic_max_output_tokens("anthropic/claude-opus-4.6") == 128000
    assert anthropic_max_output_tokens("anthropic/claude-sonnet-4") == 64000
    assert anthropic_max_output_tokens("anthropic/claude-haiku-4.5") == 64000


def test_clamp_anthropic_max_tokens() -> None:
    assert clamp_anthropic_max_tokens("claude-haiku-4-5-20251001", 128000) == 64000
    assert clamp_anthropic_max_tokens("claude-opus-4-6", 128000) == 128000
