"""Typed CapabilityDispatch objects — the per-model dispatch DATA (thread 1234).

These are the libs-resident typed reshape of the deleted static maps
(``_ANTHROPIC_MAX_OUTPUT_TOKENS``, ``_REASONING_EFFORT_BUDGET_TOKENS``,
``_ANTHROPIC_ADAPTIVE_MODELS``, ``_DEFAULT_HIGH_EFFORT_MODELS``). They carry
DATA only — the native-field translation MECHANISM lives in the
``ModelWrapper`` hierarchy (``wrappers.py``). Per Option B (assertion 13136),
the registry built from these objects is the SOLE authoritative cloud
dispatch-data source; no adapter-local capability constant survives.

The schema-of-record pydantic mirror rides ``ModelCapabilities`` on the gateway
(``services/_universal-llm-gateway/src/schemas/capabilities.py``). This module
must NOT import that schema ([universal:libs-first]).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

OverCeiling = Literal["clamp", "reject"]
ReasoningValueKind = Literal["effort_string", "token_budget", "adaptive"]

# Sentinel distinguishing "inject this explicit default value" from
# "omit and let the provider default" on a knob (G9). A KnobSpec whose
# ``default`` is OMIT means an absent caller value is left absent, never
# coerced to a wrong explicit default.
OMIT: Final = object()


@dataclass(frozen=True, slots=True)
class KnobViolation:
    """One rejected knob in the G9 collect-all-violations envelope."""

    knob: str
    reject_code: Literal[
        "unsupported_by_surface",
        "unsupported_by_model",
        "deleted_knob",
        "conflicting_knobs",
    ]
    message: str


class ProtocolError(Exception):
    """G9 reject-loudly: a structured collect-all-violations error.

    Replaces the adapter's silent ``logger.warning`` drop of unsupported
    knobs. Carries EVERY rejected knob (not first-fail) so the caller sees
    the full set in one error.
    """

    def __init__(self, violations: list[KnobViolation]) -> None:
        self.violations = list(violations)
        detail = "; ".join(f"{v.knob}: {v.reject_code}" for v in self.violations)
        super().__init__(f"unsupported dispatch knobs rejected: {detail}")


class ContextWindowExceededError(Exception):
    """Input alone leaves no usable generation space within the context window.

    Raised at the single dispatch boundary when ``context_window − input_tokens``
    does not exceed the safety buffer — i.e. the prompt is genuinely over-limit,
    so no positive ``max_output`` can be requested without
    ``input + max_output > context_window`` (the provider
    ``context_length_exceeded`` failure this guard pre-empts). Surfaces as a
    structured terminal error + observability event rather than an opaque
    provider 400. Distinct from a mere ceiling clamp (which still has room).
    """

    def __init__(
        self,
        *,
        model: str,
        context_window: int,
        input_tokens: int,
        safety_buffer: int,
    ) -> None:
        self.model = model
        self.context_window = context_window
        self.input_tokens = input_tokens
        self.safety_buffer = safety_buffer
        self.available = context_window - input_tokens - safety_buffer
        super().__init__(
            f"input over context limit for {model!r}: input_tokens={input_tokens} "
            f"+ safety_buffer={safety_buffer} leaves no room within "
            f"context_window={context_window} (available={self.available})"
        )


class CatalogMissError(Exception):
    """G13: a model that resolves to no capability card is a structural
    fail-fast, never a silent default.

    Raised when the dispatch provider/surface cannot be determined at all
    (provider-uninferable) or when an Anthropic family matches no capability
    card in the registry ceiling table (``miss_reason="no_capability_card"``).
    The ``miss_reason`` field distinguishes the cases. Admission rejects rather
    than dispatching on a guessed ceiling and an unguessable thinking surface.
    """

    def __init__(self, miss_key: str, miss_reason: str) -> None:
        self.miss_key = miss_key
        self.miss_reason = miss_reason
        super().__init__(f"catalog-miss for {miss_key!r}: {miss_reason}")


@dataclass(frozen=True, slots=True)
class CapabilityMaxOutput:
    """Max-output resolution DATA (G1 reshape: floor + ceiling + default + policy).

    - ``ceiling``: hard upper bound (Anthropic clamps to it). ``None`` = no
      ceiling (Responses / Google).
    - ``floor``: minimum effective value the surface bumps UP to (Responses
      bumps to 16384). ``None`` = no floor.
    - ``default``: value used when the caller omits ``max_tokens`` (Anthropic
      uses the model ceiling; Responses/Google use 131072).
    - ``over_ceiling``: ``clamp`` (default) or ``reject`` when a requested value
      exceeds ``ceiling``.
    - ``native_field``: the verbatim provider body field the resolved value is
      written to (``max_tokens`` / ``max_output_tokens`` / ``maxOutputTokens``).
    - ``context_window``: total input+output token budget (the provider context
      window). Static, manually-curated capability fact (researched per model).
      ``None`` = uncurated → the boundary applies no input-aware clamp for the
      model (current behavior preserved). Drives the input-aware output budget
      ``min(ceiling, context_window − input_tokens − buffer)`` at the boundary.
    """

    default: int
    native_field: str
    ceiling: int | None = None
    floor: int | None = None
    over_ceiling: OverCeiling = "clamp"
    context_window: int | None = None


@dataclass(frozen=True, slots=True)
class CapabilityReasoningDispatch:
    """Reasoning-effort dispatch DATA.

    ``value_kind`` drives a TYPED setter in the ``ModelWrapper`` subclass
    MECHANISM (G6 + G12) — it is NOT path-substitution:
      - ``adaptive``: subclass emits the provider adaptive object
        (Anthropic ``{"type": "adaptive"}``).
      - ``token_budget``: subclass emits a budget object from ``budget_map``
        (Anthropic ``{"type": "enabled", "budget_tokens": N}``); efforts absent
        from the map are rejected at the G9 boundary (422 with valid keys).
      - ``effort_string``: subclass emits the literal effort
        (OpenAI/xAI/Google ``{"effort": <e>}``).

    ``accepted_values`` is the support predicate (the reshaped
    ``_xai/_openai_supports_reasoning_effort``); ``default`` is the implicit
    model default effort (reshaped ``_DEFAULT_HIGH_EFFORT_MODELS``).
    """

    native_field_path: str
    value_kind: ReasoningValueKind
    accepted_values: tuple[str, ...]
    default: str | None = None
    budget_map: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class KnobSpec:
    """A single declared generation knob and its acceptance policy.

    ``default is OMIT`` ⟺ an absent caller value is left absent (G9 sentinel).
    """

    name: str
    accepted: tuple[str, ...] | None = None
    default: object = OMIT


@dataclass(frozen=True, slots=True)
class CapabilitySpecializations:
    """G3 typed-closed specializations seam — concrete provider-native facts only.

    No open escape hatch (NOT ``extra="allow"``). Enumerates known kinds:
    ``unsupported_values`` (knob/value pairs the model rejects) and a single
    ``behavioral_note``, each paired with an ``evidence_uri``.
    """

    unsupported_values: tuple[tuple[str, str], ...] = ()
    behavioral_note: str | None = None
    evidence_uri: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityDispatch:
    """The per-model dispatch facet: api_surface + the typed knob DATA.

    Resolved per model from the libs registry; hydrated into a ``ModelWrapper``
    whose subclass (keyed by ``api_surface``) owns the translation mechanism.
    """

    api_surface: str
    max_output: CapabilityMaxOutput
    reasoning: CapabilityReasoningDispatch | None = None
    params: Mapping[str, KnobSpec] = field(default_factory=dict)
    specializations: CapabilitySpecializations | None = None
