"""Input-aware output-budget clamp at the single dispatch boundary (P0).

Proves the math + wiring with DETERMINISTIC token counts — no live provider
spend. The guarantee: when both ``input_tokens`` and ``context_window`` are
supplied, ``resolve_dispatch`` never resolves a ``max_output`` that would make
``input + max_output > context_window``; a genuinely over-limit prompt raises a
structured ``ContextWindowExceededError`` rather than producing an opaque
provider ``context_length_exceeded`` 400.

Parity guard: when either input-aware value is omitted the resolution is
identical to the pre-change boundary (covered by ``test_max_output_parity``).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.offline

from llm_adapters.capability_dispatch import (  # noqa: E402
    DEFAULT_INPUT_SAFETY_BUFFER,
    ContextWindowExceededError,
    resolve_dispatch,
)

# Anthropic context window (1M-class frontier family); output ceiling 128000.
_CTX = 1_000_000
_OPUS = "anthropic/claude-opus-4-8"


def test_no_input_args_is_unchanged_default() -> None:
    """Omitting input_tokens → model-max default; static window surfaces, no clamp."""
    res = resolve_dispatch(_OPUS).max_output
    assert res.resolved == 128000
    assert res.decision == "default"
    assert res.input_tokens is None
    # context_window surfaces the static capability value (transparency) but
    # cannot clamp without an input count.
    assert res.context_window == 1_000_000


def test_input_tokens_uses_static_registry_context_window() -> None:
    """input_tokens alone clamps via the model's STATIC capability context window.

    ``capability defines everything static``: opus-4-8 carries context_window=1M
    in the registry, so a large input clamps without the caller passing a window.
    """
    res = resolve_dispatch(_OPUS, input_tokens=920_000).max_output
    assert res.context_window == 1_000_000
    assert res.decision == "input_clamp"
    assert res.resolved == 1_000_000 - 920_000 - DEFAULT_INPUT_SAFETY_BUFFER


def test_small_input_no_explicit_window_keeps_default() -> None:
    """Small input + static 1M window → model-max default, no clamp."""
    res = resolve_dispatch(_OPUS, input_tokens=1000).max_output
    assert res.resolved == 128000
    assert res.decision == "default"
    assert res.context_window == 1_000_000


def test_no_input_tokens_never_clamps_even_with_static_window() -> None:
    """Absent input count → max-allowable output, regardless of static window."""
    res = resolve_dispatch(_OPUS).max_output
    assert res.resolved == 128000
    assert res.decision == "default"


def test_responses_surface_static_context_windows() -> None:
    """Responses-surface models carry curated static context windows."""
    gpt = resolve_dispatch("openai/gpt-5.5").max_output
    assert gpt.context_window == 1_050_000
    grok = resolve_dispatch("xai/grok-4.3").max_output
    assert grok.context_window == 1_000_000
    legacy = resolve_dispatch("openai/gpt-5.2").max_output
    assert legacy.context_window == 200_000


def test_responses_large_input_clamps_with_static_window() -> None:
    """gpt-5.5 with huge input clamps via static 1.05M window (no live spend)."""
    input_tokens = 1_000_000
    res = resolve_dispatch("openai/gpt-5.5", input_tokens=input_tokens).max_output
    assert res.context_window == 1_050_000
    assert res.decision == "input_clamp"
    assert res.resolved == 1_050_000 - input_tokens - DEFAULT_INPUT_SAFETY_BUFFER


def test_responses_unknown_family_gets_conservative_default_window() -> None:
    """Unmarked Responses model falls back to 200k conservative default."""
    res = resolve_dispatch("openai/unknown-future-model-xyz").max_output
    assert res.context_window == 200_000


def test_small_input_leaves_model_max_default() -> None:
    """Input well within the window → default model-max output, decision unchanged."""
    res = resolve_dispatch(_OPUS, input_tokens=1000, context_window=_CTX).max_output
    assert res.resolved == 128000
    assert res.decision == "default"
    assert res.input_tokens == 1000
    assert res.context_window == _CTX


def test_large_input_clamps_below_ceiling() -> None:
    """Large input shrinks the budget so input + max_output ≤ context_window."""
    input_tokens = 920_000
    res = resolve_dispatch(
        _OPUS, input_tokens=input_tokens, context_window=_CTX
    ).max_output
    expected = _CTX - input_tokens - DEFAULT_INPUT_SAFETY_BUFFER
    assert res.resolved == expected
    assert res.decision == "input_clamp"
    assert input_tokens + res.resolved <= _CTX


def test_explicit_request_also_input_clamped() -> None:
    """An explicit (large) max_tokens is still clamped to remaining room."""
    input_tokens = 950_000
    res = resolve_dispatch(
        _OPUS,
        requested_max_output=128000,
        input_tokens=input_tokens,
        context_window=_CTX,
    ).max_output
    assert res.resolved == _CTX - input_tokens - DEFAULT_INPUT_SAFETY_BUFFER
    assert res.decision == "input_clamp"


def test_explicit_small_request_under_room_not_clamped() -> None:
    """When the explicit request already fits the remaining room, keep it."""
    res = resolve_dispatch(
        _OPUS,
        requested_max_output=4096,
        input_tokens=500_000,
        context_window=_CTX,
    ).max_output
    assert res.resolved == 4096
    assert res.decision == "explicit"


def test_over_limit_input_raises_structured_terminal_error() -> None:
    """Input that leaves no room beyond the buffer → structured terminal error."""
    input_tokens = _CTX  # input alone fills the window
    with pytest.raises(ContextWindowExceededError) as exc:
        resolve_dispatch(_OPUS, input_tokens=input_tokens, context_window=_CTX)
    err = exc.value
    assert err.model == _OPUS
    assert err.context_window == _CTX
    assert err.input_tokens == input_tokens
    assert err.available < 1


def test_input_exactly_at_buffer_boundary_raises() -> None:
    """available < 1 (input + buffer == window) is over-limit → raise."""
    input_tokens = _CTX - DEFAULT_INPUT_SAFETY_BUFFER
    with pytest.raises(ContextWindowExceededError):
        resolve_dispatch(_OPUS, input_tokens=input_tokens, context_window=_CTX)


def test_one_token_of_room_clamps_to_one() -> None:
    """available == 1 is the smallest non-raising budget."""
    input_tokens = _CTX - DEFAULT_INPUT_SAFETY_BUFFER - 1
    res = resolve_dispatch(
        _OPUS, input_tokens=input_tokens, context_window=_CTX
    ).max_output
    assert res.resolved == 1
    assert res.decision == "input_clamp"


def test_custom_safety_buffer_respected() -> None:
    """An explicit larger buffer reserves more headroom."""
    input_tokens = 900_000
    res = resolve_dispatch(
        _OPUS,
        input_tokens=input_tokens,
        context_window=_CTX,
        input_safety_buffer=5000,
    ).max_output
    assert res.resolved == _CTX - input_tokens - 5000


def test_resolved_event_fields_carry_input_context() -> None:
    """Observability payload surfaces the input-aware accounting."""
    fields = resolve_dispatch(
        _OPUS, input_tokens=920_000, context_window=_CTX
    ).resolved_event_fields()
    assert fields["max_output_input_tokens"] == 920_000
    assert fields["max_output_context_window"] == _CTX
    assert fields["max_output_decision"] == "input_clamp"
