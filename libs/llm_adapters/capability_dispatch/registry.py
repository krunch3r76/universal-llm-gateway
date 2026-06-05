"""Libs-resident CapabilityDispatch registry — SOLE authoritative cloud source.

Option B (assertion 13136): the deleted static maps are reshaped here, keyed by
``ModelId.normalized`` (for cloud models ``.normalized`` == full
``provider/model``). All three stacks (F / CP / WB) resolve through this ONE
typed lookup; no adapter-local capability constant survives the G4 grep.

Resolution is provider-surface scoped: the Anthropic max-output ceiling carries
the per-model family table (reshaped ``_ANTHROPIC_MAX_OUTPUT_TOKENS``) with the
8192 unknown-claude fail-closed ceiling; OpenAI/xAI (Responses) and Google
carry surface-uniform floor/default. Reasoning dispatch is family-scoped
(adaptive vs budget-mode vs effort-string).

G13: a provider-uninferable model is a structural ``CatalogMissError`` — never a
silent default. The in-surface unknown-claude ceiling is NOT a catalog-miss.
"""

from __future__ import annotations

from model_id import ModelId, infer_cloud_provider_from_bare

from .types import (
    CapabilityDispatch,
    CapabilityMaxOutput,
    CapabilityReasoningDispatch,
    CatalogMissError,
)

# Accepted reasoning-effort vocabulary (union of documented provider surfaces).
# Kept in lockstep with frontier ``_VALID_REASONING_EFFORTS``.
VALID_REASONING_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)
_ALL_EFFORTS: tuple[str, ...] = tuple(sorted(VALID_REASONING_EFFORTS))

# ── Anthropic max-output ceilings (reshaped _ANTHROPIC_MAX_OUTPUT_TOKENS) ──────
# Ordered most-specific first; substring match reproduces the OLD helper.
_ANTHROPIC_MAX_OUTPUT_CEILINGS: tuple[tuple[str, int], ...] = (
    ("claude-opus-4-8", 128000),
    ("claude-opus-4.8", 128000),
    ("claude-opus-4-7", 128000),
    ("claude-opus-4.7", 128000),
    ("claude-opus-4-6", 128000),
    ("claude-opus-4.6", 128000),
    ("claude-sonnet-4-6", 64000),
    ("claude-sonnet-4.6", 64000),
    ("claude-haiku-4-5-20251001", 64000),
    ("claude-haiku-4-5", 64000),
    ("claude-haiku-4.5", 64000),
    ("claude-opus-4-5-20251101", 64000),
    ("claude-opus-4-5", 64000),
    ("claude-opus-4.5", 64000),
    ("claude-sonnet-4-5-20250929", 64000),
    ("claude-sonnet-4-5", 64000),
    ("claude-sonnet-4.5", 64000),
    ("claude-opus-4-1-20250805", 32000),
    ("claude-opus-4-1", 32000),
    ("claude-opus-4.1", 32000),
    ("claude-sonnet-4-20250514", 64000),
    ("claude-sonnet-4-0", 64000),
    ("claude-sonnet-4", 64000),
    ("claude-opus-4-20250514", 32000),
    ("claude-opus-4-0", 32000),
    ("claude-opus-4", 32000),
    ("claude-3-5-sonnet", 8192),
    ("claude-3-5-haiku", 8192),
    ("claude-3-opus", 4096),
    ("claude-3-sonnet", 4096),
    ("claude-3-haiku", 4096),
)
# Fail-closed conservative ceiling for unknown claude models (within-surface,
# NOT a catalog-miss).
_ANTHROPIC_UNKNOWN_CEILING = 8192

# ── Reasoning families (reshaped frontier_dispatch_request maps) ──────────────
# Adaptive-capable Anthropic families (per adaptive-thinking.md).
_ANTHROPIC_ADAPTIVE_FAMILIES: frozenset[str] = frozenset(
    {
        "claude-mythos-preview",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
    }
)
# Budget-mode (pre-adaptive Anthropic) token map.
_REASONING_BUDGET_MAP: dict[str, int] = {"low": 2048, "medium": 8192, "high": 24000}
# Implicit default reasoning_effort by ``provider/model`` key.
_DEFAULT_HIGH_EFFORT: frozenset[str] = frozenset({"xai/grok-4.3"})

# ── Responses / Google surface-uniform max-output ────────────────────────────
_RESPONSES_FLOOR = 16384
_RESPONSES_DEFAULT = 131072
_GOOGLE_DEFAULT = 131072

# ── api_surface keys ──────────────────────────────────────────────────────────
SURFACE_ANTHROPIC = "anthropic"
SURFACE_OPENAI_RESPONSES = "openai_responses"
SURFACE_OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
SURFACE_GOOGLE = "google_generate_content"

_PROVIDER_SURFACE: dict[str, str] = {
    "anthropic": SURFACE_ANTHROPIC,
    "openai": SURFACE_OPENAI_RESPONSES,
    "chatgpt": SURFACE_OPENAI_RESPONSES,
    "xai": SURFACE_OPENAI_RESPONSES,
    "google": SURFACE_GOOGLE,
}


def _resolve_provider(model: str | ModelId) -> tuple[str, str]:
    """Return (provider, bare_model). G13 fail-fast when provider is uninferable."""
    parsed = ModelId.parse(model)
    if parsed.provider:
        return parsed.provider, parsed.api_model_id
    inferred = infer_cloud_provider_from_bare(parsed.base_id)
    if inferred is None:
        raise CatalogMissError(
            str(model), "provider could not be inferred from model id"
        )
    return inferred, parsed.api_model_id


def _anthropic_ceiling(bare_model: str) -> int:
    normalized = bare_model.strip().lower()
    for marker, limit in _ANTHROPIC_MAX_OUTPUT_CEILINGS:
        if marker in normalized:
            return limit
    return _ANTHROPIC_UNKNOWN_CEILING


def _anthropic_uses_adaptive(bare_model: str) -> bool:
    normalized = bare_model.strip().lower()
    return normalized in _ANTHROPIC_ADAPTIVE_FAMILIES


def xai_supports_reasoning_effort(model: str) -> bool:
    """grok-3 family, grok-4.3, grok-4.20-multi-agent accept reasoning.effort.

    Reshaped verbatim from the deleted ``reasoning_capabilities`` predicate.
    """
    return any(
        prefix in model
        for prefix in ("grok-3-mini", "grok-3", "grok-4.3", "grok-4.20-multi-agent")
    )


def openai_supports_reasoning_effort(model: str) -> bool:
    """o-series + gpt-5 family accept reasoning.effort.

    Reshaped verbatim from the deleted ``reasoning_capabilities`` predicate.
    """
    return any(model.startswith(prefix) for prefix in ("o1", "o3", "o4", "gpt-5"))


def default_reasoning_effort(model: str | ModelId) -> str | None:
    """Implicit default reasoning_effort for a model, or None.

    Reshaped from ``_DEFAULT_HIGH_EFFORT_MODELS`` + ``resolve_default_reasoning_effort``.
    """
    if model is None or (isinstance(model, str) and not model.strip()):
        return None
    provider, bare = _resolve_provider(model)
    if f"{provider}/{bare}" in _DEFAULT_HIGH_EFFORT:
        return "high"
    return None


def _build_reasoning(provider: str, bare_model: str) -> CapabilityReasoningDispatch:
    if provider == "anthropic":
        if _anthropic_uses_adaptive(bare_model):
            return CapabilityReasoningDispatch(
                native_field_path="thinking",
                value_kind="adaptive",
                accepted_values=_ALL_EFFORTS,
            )
        return CapabilityReasoningDispatch(
            native_field_path="thinking",
            value_kind="token_budget",
            accepted_values=tuple(sorted(_REASONING_BUDGET_MAP)),
            budget_map=dict(_REASONING_BUDGET_MAP),
        )
    surface = _PROVIDER_SURFACE.get(provider, SURFACE_OPENAI_RESPONSES)
    field_path = "thinkingConfig" if surface == SURFACE_GOOGLE else "reasoning.effort"
    default = "high" if f"{provider}/{bare_model}" in _DEFAULT_HIGH_EFFORT else None
    return CapabilityReasoningDispatch(
        native_field_path=field_path,
        value_kind="effort_string",
        accepted_values=_ALL_EFFORTS,
        default=default,
    )


def _build_max_output(provider: str, bare_model: str) -> CapabilityMaxOutput:
    if provider == "anthropic":
        ceiling = _anthropic_ceiling(bare_model)
        return CapabilityMaxOutput(
            default=ceiling,
            native_field="max_tokens",
            ceiling=ceiling,
            floor=None,
            over_ceiling="clamp",
        )
    if provider == "google":
        return CapabilityMaxOutput(
            default=_GOOGLE_DEFAULT,
            native_field="maxOutputTokens",
            ceiling=None,
            floor=None,
            over_ceiling="clamp",
        )
    # openai / chatgpt / xai → Responses API surface.
    return CapabilityMaxOutput(
        default=_RESPONSES_DEFAULT,
        native_field="max_output_tokens",
        ceiling=None,
        floor=_RESPONSES_FLOOR,
        over_ceiling="clamp",
    )


def resolve(model: str | ModelId) -> CapabilityDispatch:
    """Resolve the typed ``CapabilityDispatch`` for a cloud model.

    Lookup is keyed on the resolved provider/surface; the Anthropic ceiling is
    drawn from the reshaped family table. G13: provider-uninferable → fail-fast.
    """
    provider, bare = _resolve_provider(model)
    surface = _PROVIDER_SURFACE.get(provider)
    if surface is None:
        raise CatalogMissError(
            str(model), f"no dispatch surface for provider={provider!r}"
        )
    return CapabilityDispatch(
        api_surface=surface,
        max_output=_build_max_output(provider, bare),
        reasoning=_build_reasoning(provider, bare),
    )
