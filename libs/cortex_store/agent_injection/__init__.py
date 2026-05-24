"""Phase 1.0a public API."""

from .errors import (
    AgentInjectionAdmissionError,
    AgentInjectionError,
    SelectionError,
    TemplateRenderError,
    ViolationDetail,
)
from .materializers import (
    compute_d2_content_hash,
    materialize_d1,
    materialize_d2,
    materialize_d3,
    materialize_d4,
)
from .selection import STRATEGIES, select
from .templates import (
    D1_TEMPLATE,
    D2_TEMPLATE,
    D3_TEMPLATE,
    D4_TEMPLATE,
    render_d1,
    render_d2,
    render_d3,
    render_d4,
)
from .validator_output import (
    BRIEF_DOMAINS,
    Finding,
    OutputValidationResult,
    validate_output,
)
from .validator_preflight import ValidationResult, preflight_validate

__version__ = "phase-1.0b"

__all__ = [
    "__version__",
    "AgentInjectionError",
    "AgentInjectionAdmissionError",
    "TemplateRenderError",
    "SelectionError",
    "ViolationDetail",
    "D1_TEMPLATE",
    "D2_TEMPLATE",
    "D3_TEMPLATE",
    "D4_TEMPLATE",
    "render_d1",
    "render_d2",
    "render_d3",
    "render_d4",
    "materialize_d1",
    "materialize_d2",
    "materialize_d3",
    "materialize_d4",
    "compute_d2_content_hash",
    "select",
    "STRATEGIES",
    "preflight_validate",
    "ValidationResult",
    # output validation (1.0b)
    "validate_output",
    "OutputValidationResult",
    "Finding",
    "BRIEF_DOMAINS",
]
