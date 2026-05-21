"""Reasoning-effort capability predicates for Responses API vendors."""

from __future__ import annotations


def _xai_supports_reasoning_effort(model: str) -> bool:
    """grok-3 family, grok-4.3, and grok-4.20-multi-agent accept reasoning.effort.

    Other grok-4.20 variants (-reasoning, -non-reasoning) reject reasoningEffort
    with HTTP 400 'Model does not support parameter reasoningEffort' (verified
    direct against /v1/responses 2026-05-20 — same finding as 2026-03-31).
    grok-4.3 explicitly supports reasoning.effort per xAI docs.
    grok-4.20-multi-agent-0309 honors effort by scaling its internal swarm
    (low/medium → ~4 agents, high/xhigh → ~16 agents); empirically verified
    2026-05-20 (cortex 10603) — low→high reasoning_tokens 1081→10736
    (~9.9x), input_tokens 3631→49658 (~13.7x).
    """
    return any(
        prefix in model
        for prefix in (
            "grok-3-mini",
            "grok-3",
            "grok-4.3",
            "grok-4.20-multi-agent",
        )
    )


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
