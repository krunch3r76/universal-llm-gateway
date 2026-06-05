"""Lane A offline registry-coherence tests (G2 anti-drift CI, SoT §3).

Asserts the internal invariants of ``capability_dispatch.registry`` with NO
provider calls. Every test mirrors a named invariant from the design SoT
(thread 1310, §3). Together with ``test_max_output_parity.py`` these two
modules form the offline Lane A that runs on every PR in GitHub CI.

Coverage:
- Every ``_PROVIDER_SURFACE`` provider resolves to a ``CapabilityDispatch``.
- A provider-uninferable model id raises ``CatalogMissError`` (G13).
- Every ``_ANTHROPIC_MAX_OUTPUT_CEILINGS`` marker resolves to its declared ceiling.
- An unknown-claude bare id → fail-closed 8192 ceiling (NOT a CatalogMissError).
- Reasoning ``value_kind`` ⇔ surface: adaptive families → ``adaptive``;
  pre-adaptive anthropic → ``token_budget`` with non-empty ``budget_map``;
  Responses / Google → ``effort_string``.
- All four ``max_output_decision`` labels reachable on representative inputs.
- ``over_ceiling="reject"`` raises ``ProtocolError``.
- OMIT sentinel: absent reasoning_effort → no native field injected.
"""

from __future__ import annotations

import pytest

from llm_adapters.capability_dispatch import (
    CatalogMissError,
    ProtocolError,
    resolve_dispatch,
    wrapper_for,
)
from llm_adapters.capability_dispatch.registry import (
    _ANTHROPIC_ADAPTIVE_FAMILIES,
    _ANTHROPIC_MAX_OUTPUT_CEILINGS,
    _ANTHROPIC_UNKNOWN_CEILING,
    _PROVIDER_SURFACE,
    SURFACE_ANTHROPIC,
    resolve,
)
from llm_adapters.capability_dispatch.types import (
    OMIT,
    CapabilityDispatch,
    CapabilityMaxOutput,
    KnobSpec,
)
from llm_adapters.capability_dispatch.wrappers import AnthropicWrapper

# One representative model per provider in _PROVIDER_SURFACE.
_PROVIDER_REPRESENTATIVE: dict[str, str] = {
    "anthropic": "anthropic/claude-sonnet-4-6",
    "openai": "openai/gpt-5.5",
    "chatgpt": "chatgpt/gpt-4o",
    "xai": "xai/grok-4.3",
    "google": "google/gemini-3-pro",
}

# Pre-adaptive Anthropic models (token_budget, not adaptive).
_PRE_ADAPTIVE = (
    "claude-3-5-sonnet",
    "claude-3-opus",
    "claude-sonnet-4-20250514",
)


# ── §3.1: every _PROVIDER_SURFACE provider resolves ──────────────────────────


@pytest.mark.parametrize("provider", sorted(_PROVIDER_SURFACE))
def test_every_provider_surface_resolves(provider: str) -> None:
    """resolve() must not raise for any registered provider."""
    model = _PROVIDER_REPRESENTATIVE[provider]
    dispatch = resolve(model)
    assert dispatch.api_surface == _PROVIDER_SURFACE[provider]


# ── §3.2: provider-uninferable → CatalogMissError (G13) ──────────────────────


def test_provider_uninferable_raises_catalog_miss() -> None:
    """A completely unknown model id must fail loudly, never silently default."""
    with pytest.raises(CatalogMissError):
        resolve("xyzzy-completelyunknown-vendor-99999")


# ── §3.3: every Anthropic ceiling marker resolves ────────────────────────────


@pytest.mark.parametrize("marker,expected_ceiling", _ANTHROPIC_MAX_OUTPUT_CEILINGS)
def test_anthropic_ceiling_marker_resolves(marker: str, expected_ceiling: int) -> None:
    """Every _ANTHROPIC_MAX_OUTPUT_CEILINGS marker must resolve to its ceiling."""
    dispatch = resolve(f"anthropic/{marker}")
    assert dispatch.max_output.ceiling == expected_ceiling


# ── §3.4: unknown-claude → fail-closed 8192 (NOT CatalogMissError) ───────────


def test_unknown_claude_uses_fail_closed_ceiling() -> None:
    """Unknown claude bare id resolves to within-surface 8192 ceiling, not miss."""
    dispatch = resolve("anthropic/claude-unknown-xyz-model")
    assert dispatch.max_output.ceiling == _ANTHROPIC_UNKNOWN_CEILING
    assert dispatch.api_surface == SURFACE_ANTHROPIC


def test_unknown_claude_does_not_raise() -> None:
    """Confirms unknown-claude is not a CatalogMissError (within-surface fallback)."""
    try:
        resolve("anthropic/claude-totally-unknown-9999")
    except CatalogMissError:
        pytest.fail("unknown-claude must not raise CatalogMissError")


# ── §3.5: reasoning value_kind ⇔ surface ─────────────────────────────────────


@pytest.mark.parametrize("family", sorted(_ANTHROPIC_ADAPTIVE_FAMILIES))
def test_reasoning_value_kind_adaptive_families(family: str) -> None:
    """Anthropic adaptive families → value_kind='adaptive'."""
    dispatch = resolve(f"anthropic/{family}")
    assert dispatch.reasoning is not None
    assert dispatch.reasoning.value_kind == "adaptive"


@pytest.mark.parametrize("model", _PRE_ADAPTIVE)
def test_reasoning_value_kind_pre_adaptive_token_budget(model: str) -> None:
    """Pre-adaptive Anthropic → value_kind='token_budget' with non-empty budget_map."""
    dispatch = resolve(f"anthropic/{model}")
    assert dispatch.reasoning is not None
    assert dispatch.reasoning.value_kind == "token_budget"
    assert dispatch.reasoning.budget_map  # non-empty


@pytest.mark.parametrize(
    "model",
    ["openai/gpt-5.5", "xai/grok-4.3", "google/gemini-3-pro"],
)
def test_reasoning_value_kind_effort_string(model: str) -> None:
    """Responses and Google surfaces → value_kind='effort_string'."""
    dispatch = resolve(model)
    assert dispatch.reasoning is not None
    assert dispatch.reasoning.value_kind == "effort_string"


# ── §3.6: all four max_output_decision labels reachable ──────────────────────


def test_decision_label_explicit() -> None:
    """Within-ceiling request → 'explicit' decision."""
    wrapper = wrapper_for("anthropic/claude-opus-4-8")
    _, decision = wrapper.resolve_max_output(50000)
    assert decision == "explicit"


def test_decision_label_default() -> None:
    """No request → 'default' decision."""
    wrapper = wrapper_for("anthropic/claude-opus-4-8")
    _, decision = wrapper.resolve_max_output(None)
    assert decision == "default"


def test_decision_label_floor_bump() -> None:
    """Sub-floor Responses request → 'floor_bump' decision."""
    wrapper = wrapper_for("openai/gpt-5.5")
    resolved, decision = wrapper.resolve_max_output(1000)
    assert decision == "floor_bump"
    assert resolved == 16384  # floor value


def test_decision_label_ceiling_clamp() -> None:
    """Over-ceiling Anthropic request → 'ceiling_clamp' decision."""
    wrapper = wrapper_for("anthropic/claude-opus-4-8")
    resolved, decision = wrapper.resolve_max_output(200000)
    assert decision == "ceiling_clamp"
    assert resolved == 128000  # ceiling value


# ── §3.7: over_ceiling="reject" raises ProtocolError ─────────────────────────


def test_over_ceiling_reject_raises_protocol_error() -> None:
    """A CapabilityMaxOutput with over_ceiling='reject' must raise ProtocolError."""
    reject_dispatch = CapabilityDispatch(
        api_surface=SURFACE_ANTHROPIC,
        max_output=CapabilityMaxOutput(
            default=4096,
            native_field="max_tokens",
            ceiling=4096,
            over_ceiling="reject",
        ),
    )
    wrapper = AnthropicWrapper(reject_dispatch)
    with pytest.raises(ProtocolError):
        wrapper.resolve_max_output(10000)


# ── §3.8: OMIT sentinel honored ──────────────────────────────────────────────


def test_knob_spec_default_is_omit() -> None:
    """KnobSpec.default must be OMIT when not explicitly set."""
    spec = KnobSpec(name="test_knob")
    assert spec.default is OMIT


def test_omit_absent_reasoning_not_injected() -> None:
    """No reasoning_effort requested → native field stays absent (not injected)."""
    result = resolve_dispatch("openai/gpt-5.5")
    assert result.reasoning.native is None
    assert result.reasoning.effort is None
