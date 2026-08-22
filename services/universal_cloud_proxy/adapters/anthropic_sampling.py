"""Strip sampling knobs Anthropic Claude 5 / Opus 4.7+ reject with 400.

Mirrors ``openai_compatible._strip_reasoning_incompatible_params``. Claude Sonnet 5
(and Opus 4.7+) return ``temperature is deprecated for this model`` when any of
``temperature`` / ``top_p`` / ``top_k`` is set to a non-default value. Prefix
match future-proofs dated revisions (``claude-sonnet-5-…``) without catching
Claude 4.6 and earlier, which still accept sampling.
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_event_bus.events.debug import emit_debug_event

# Bare-id prefixes after provider strip (anthropic/claude-sonnet-5 → claude-sonnet-5).
# Do not add claude-sonnet-4 / claude-opus-4 — those families still accept sampling.
_CLAUDE5_SAMPLING_BLOCKED_PREFIXES: tuple[str, ...] = (
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-5",
    "claude-fable-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
)
_CLAUDE5_UNSUPPORTED_PARAMS: tuple[str, ...] = ("temperature", "top_p", "top_k")


def _bare_upstream_model(upstream_model: str) -> str:
    """Return the provider-bare model id, lowercased."""
    return upstream_model.strip().lower().rsplit("/", 1)[-1]


def _is_claude5_sampling_blocked_model(upstream_model: str) -> bool:
    """True iff the upstream model rejects non-default sampling knobs."""
    bare = _bare_upstream_model(upstream_model)
    return any(bare.startswith(prefix) for prefix in _CLAUDE5_SAMPLING_BLOCKED_PREFIXES)


def _strip_claude5_incompatible_params(
    body: dict[str, Any],
    *,
    upstream_model: str,
) -> list[str]:
    """Drop sampling knobs Claude 5 / Opus 4.7+ reject. Returns stripped keys.

    Mutates ``body`` in place. No-op unless the model is in the blocked family;
    Claude 4.6 and earlier pass through unchanged.
    """
    if not _is_claude5_sampling_blocked_model(upstream_model):
        return []
    stripped = [key for key in _CLAUDE5_UNSUPPORTED_PARAMS if key in body]
    for key in stripped:
        body.pop(key, None)
    return stripped


def _emit_strip_debug(upstream_model: str, stripped: list[str], surface: str) -> None:
    """Fire-and-forget debug signal when Claude 5 sampling knobs were dropped."""
    if not stripped:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    coro = emit_debug_event(
        "debug.cloud.params.stripped",
        {
            "provider": "anthropic",
            "model": upstream_model,
            "stripped": stripped,
            "surface": surface,
        },
        source="cloud-proxy",
        scope="global",
    )
    loop.create_task(coro)


def strip_claude5_sampling(
    body: dict[str, Any],
    *,
    upstream_model: str,
    surface: str,
) -> list[str]:
    """Strip blocked sampling knobs and emit ``debug.cloud.params.stripped``."""
    stripped = _strip_claude5_incompatible_params(body, upstream_model=upstream_model)
    _emit_strip_debug(upstream_model, stripped, surface)
    return stripped
