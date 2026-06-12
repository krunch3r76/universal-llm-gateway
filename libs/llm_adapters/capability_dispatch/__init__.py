"""Per-model CapabilityDispatch — libs-resident SOLE authoritative cloud source.

The typed registry (DATA), the ModelWrapper hierarchy (translation MECHANISM,
keyed by ``api_surface``), and the single ``resolve_dispatch`` boundary. All
three cloud stacks (F / CP / WB) resolve through this package; no adapter-local
capability constant survives the G4 grep after Fence D.
"""

from __future__ import annotations

from .boundary import (
    CATALOG_MISS_EVENT,
    CONTEXT_EXCEEDED_EVENT,
    DEFAULT_INPUT_SAFETY_BUFFER,
    KNOB_REJECTED_EVENT,
    RESOLVED_EVENT,
    DispatchResolution,
    MaxOutputResolution,
    ReasoningResolution,
    resolve_dispatch,
)
from .projection import project_knob_resolution
from .registry import (
    VALID_REASONING_EFFORTS,
    default_reasoning_effort,
    openai_supports_reasoning_effort,
    resolve,
    xai_supports_reasoning_effort,
)
from .serialization import to_wire_dict
from .types import (
    CapabilityDispatch,
    CapabilityMaxOutput,
    CapabilityReasoningDispatch,
    CapabilitySpecializations,
    CatalogMissError,
    ContextWindowExceededError,
    KnobSpec,
    KnobViolation,
    ProtocolError,
)
from .wrappers import (
    AnthropicWrapper,
    GoogleGenerateContentWrapper,
    ModelWrapper,
    OpenAIChatCompletionsWrapper,
    OpenAIResponsesWrapper,
    wrapper_for,
)

__all__ = [
    # types
    "CapabilityDispatch",
    "CapabilityMaxOutput",
    "CapabilityReasoningDispatch",
    "CapabilitySpecializations",
    "KnobSpec",
    "KnobViolation",
    "ProtocolError",
    "CatalogMissError",
    "ContextWindowExceededError",
    # registry
    "resolve",
    "VALID_REASONING_EFFORTS",
    "default_reasoning_effort",
    "xai_supports_reasoning_effort",
    "openai_supports_reasoning_effort",
    # serialization
    "to_wire_dict",
    # wrappers
    "ModelWrapper",
    "AnthropicWrapper",
    "OpenAIResponsesWrapper",
    "OpenAIChatCompletionsWrapper",
    "GoogleGenerateContentWrapper",
    "wrapper_for",
    # boundary
    "resolve_dispatch",
    "project_knob_resolution",
    "DispatchResolution",
    "MaxOutputResolution",
    "ReasoningResolution",
    "RESOLVED_EVENT",
    "KNOB_REJECTED_EVENT",
    "CATALOG_MISS_EVENT",
    "CONTEXT_EXCEEDED_EVENT",
    "DEFAULT_INPUT_SAFETY_BUFFER",
]
