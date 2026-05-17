"""Reasoning-effort capability predicates for Responses API vendors."""

from __future__ import annotations


def _xai_supports_reasoning_effort(model: str) -> bool:
    """grok-3 family and grok-4.3 accept reasoning.effort control.

    Earlier grok-4 family models (pre-grok-4.3, including -reasoning variants)
    rejected reasoningEffort despite xAI docs suggesting otherwise
    (tested 2026-03-31). grok-4.3 explicitly supports reasoning.effort with
    none/low/medium/high per current xAI docs (2026).
    """
    return any(prefix in model for prefix in ("grok-3-mini", "grok-3", "grok-4.3"))


def _openai_supports_reasoning_effort(model: str) -> bool:
    """Only OpenAI reasoning model families accept reasoning.effort.

    The o-series (o1, o3, o4-mini) and gpt-5 family (gpt-5.x, gpt-5.x-*)
    support the Responses API reasoning.effort parameter. Standard chat
    models (gpt-4o, gpt-4.1, gpt-4o-mini) reject it with
    'unsupported_parameter' — as does any model that routes through the
    Chat Completions API surface instead of Responses (those are caught
    earlier at frontier_dispatch admission via _is_chat_completions_only).
    ∀ new reasoning-capable OpenAI model: confirm it starts with one of
    these prefixes or extend this set.
    """
    return any(model.startswith(prefix) for prefix in ("o1", "o3", "o4", "gpt-5"))
