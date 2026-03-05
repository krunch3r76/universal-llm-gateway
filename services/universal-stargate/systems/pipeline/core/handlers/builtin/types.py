"""
Value types returned by and passed between builtin handler utilities.

Both types are immutable-by-design: ModelCallResult via frozen=True (per-call
data bundle, safe for concurrent map steps), RenderedPrompt via dataclass
semantics (ready-to-consume, not mutated after construction).

Kept in a dedicated module so any module can import them without pulling in
the heavier dependencies of base.py or call_model.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    """
    Immutable result from a single model invocation.

    Encapsulates all data from a single model call — enables safe concurrent
    execution by returning all per-call data together with no shared mutable state.
    """

    content: str
    finish_reason: str
    request_body: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    map_iteration_request_id: str | None
    snapshot_request_id: str | None
    system_prompt: str | None
    user_prompt: str


@dataclass(slots=True, kw_only=True)
class RenderedPrompt:
    """Ready-to-use prompt pair from PromptBuilder rendering.

    Returned by _render_prompt() — the canonical way to load a prompt from
    the registry and render it with context variables.
    """

    system_prompt: str | None
    user_prompt: str
