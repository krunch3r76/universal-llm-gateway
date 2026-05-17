"""Typed exceptions for agent_injection. AgentInjectionAdmissionError is the
canonical 422-mapped surface for preflight failures; TemplateRenderError
covers missing-template-key errors; SelectionError covers unknown strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class AgentInjectionError(Exception):
    """Base for all agent_injection errors."""


@dataclass
class ViolationDetail:
    invariant: int                    # 1..5 per D.5
    block_index: int | None = None    # which block in the packet (None for packet-level)
    detail: str = ""
    payload_excerpt: dict[str, Any] = field(default_factory=dict)


class AgentInjectionAdmissionError(AgentInjectionError):
    """Raised by preflight + materializers on D.5 invariant violations.
    Maps to HTTP 422 when wired to FastAPI. Carries violations list."""

    def __init__(self, message: str, violations: list[ViolationDetail] | None = None):
        super().__init__(message)
        self.violations = violations or []


class TemplateRenderError(AgentInjectionError):
    """Raised when render_d{1,2,3,4} is missing a required field."""


class SelectionError(AgentInjectionError):
    """Raised by select() on unknown strategy or invalid params."""
